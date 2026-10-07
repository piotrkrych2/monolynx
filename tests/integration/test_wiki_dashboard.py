"""Testy integracyjne -- dashboard wiki (drzewo stron, CRUD, upload, wyszukiwanie)."""

import json
import re
import secrets
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import select

from monolynx.main import app
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password
from monolynx.services.blog import DEFAULT_AUTHOR_NAME
from monolynx.services.blog_blocks import CONTAINER_BLOCKS, TEXT_BLOCKS, BlockDefinition
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, slug, code=None):
    """Helper: tworzy projekt z unikalnym kodem."""
    if code is None:
        code = "W" + secrets.token_hex(4).upper()
    project = Project(
        name=f"Wiki {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _login_and_add_member(client, db_session, project, email):
    """Helper: loguje uzytkownika i dodaje go jako czlonka projektu. Zwraca User."""
    await login_session(client, db_session, email=email)
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db_session.add(member)
    await db_session.flush()
    return user


async def _create_wiki_page(db_session, project, user, title="Test Page", content="# Test", parent_id=None, is_public=False):
    """Helper: tworzy strone wiki bezposrednio w DB (bez MinIO)."""
    page = WikiPage(
        project_id=project.id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
        parent_id=parent_id,
    )
    db_session.add(page)
    await db_session.flush()
    return page


# ---------------------------------------------------------------------------
# 1. Wiki index (page tree)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiIndex:
    async def test_wiki_index_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wi-auth")
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_wiki_index_empty(self, client, db_session):
        project = await _create_project(db_session, "wi-empty")
        await _login_and_add_member(client, db_session, project, "wi-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/")
        assert resp.status_code == 200
        assert "Wiki jest puste" in resp.text

    async def test_wiki_index_shows_pages(self, client, db_session):
        project = await _create_project(db_session, "wi-pages")
        user = await _login_and_add_member(client, db_session, project, "wi-pages@test.com")
        await _create_wiki_page(db_session, project, user, title="Strona Glowna")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/")
        assert resp.status_code == 200
        assert "Strona Glowna" in resp.text

    async def test_wiki_index_shows_nested_tree(self, client, db_session):
        project = await _create_project(db_session, "wi-tree")
        user = await _login_and_add_member(client, db_session, project, "wi-tree@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Parent Page")
        await _create_wiki_page(db_session, project, user, title="Child Page", parent_id=parent.id)
        resp = await client.get(f"/dashboard/{project.slug}/wiki/")
        assert resp.status_code == 200
        assert "Parent Page" in resp.text
        assert "Child Page" in resp.text

    async def test_wiki_index_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wi-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/wiki/")
        assert resp.status_code == 404

    async def test_wiki_index_has_create_link(self, client, db_session):
        project = await _create_project(db_session, "wi-link")
        await _login_and_add_member(client, db_session, project, "wi-link@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/")
        assert resp.status_code == 200
        assert f"/dashboard/{project.slug}/wiki/pages/create" in resp.text

    async def test_wiki_index_has_search_form(self, client, db_session):
        project = await _create_project(db_session, "wi-search")
        await _login_and_add_member(client, db_session, project, "wi-search@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/")
        assert resp.status_code == 200
        assert "Szukaj w wiki" in resp.text


# ---------------------------------------------------------------------------
# 2. Wiki search
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiSearch:
    async def test_search_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "ws-auth")
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/search",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.dashboard.wiki.embeddings_enabled", return_value=False)
    async def test_search_embeddings_disabled(self, mock_enabled, client, db_session):
        project = await _create_project(db_session, "ws-disabled")
        await _login_and_add_member(client, db_session, project, "ws-disabled@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/search?q=test")
        assert resp.status_code == 200
        assert "Wyszukiwanie semantyczne jest wylaczone" in resp.text

    @patch("monolynx.dashboard.wiki.embeddings_enabled", return_value=True)
    async def test_search_empty_query(self, mock_enabled, client, db_session):
        project = await _create_project(db_session, "ws-emptyq")
        await _login_and_add_member(client, db_session, project, "ws-emptyq@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/search")
        assert resp.status_code == 200
        assert "Wpisz zapytanie" in resp.text

    @patch("monolynx.dashboard.wiki.search_wiki_pages")
    @patch("monolynx.dashboard.wiki.embeddings_enabled", return_value=True)
    async def test_search_with_results(self, mock_enabled, mock_search, client, db_session):
        project = await _create_project(db_session, "ws-results")
        await _login_and_add_member(client, db_session, project, "ws-results@test.com")
        mock_search.return_value = [
            {
                "id": str(uuid.uuid4()),
                "title": "Znaleziona Strona",
                "slug": "znaleziona-strona",
                "snippet": "Fragment tekstu znaleziony w wyszukiwaniu...",
                "similarity": 0.85,
            }
        ]
        resp = await client.get(f"/dashboard/{project.slug}/wiki/search?q=testowe+zapytanie")
        assert resp.status_code == 200
        assert "Znaleziona Strona" in resp.text
        assert "1 wynikow" in resp.text

    @patch("monolynx.dashboard.wiki.search_wiki_pages")
    @patch("monolynx.dashboard.wiki.embeddings_enabled", return_value=True)
    async def test_search_no_results(self, mock_enabled, mock_search, client, db_session):
        project = await _create_project(db_session, "ws-noresults")
        await _login_and_add_member(client, db_session, project, "ws-noresults@test.com")
        mock_search.return_value = []
        resp = await client.get(f"/dashboard/{project.slug}/wiki/search?q=brakfraz")
        assert resp.status_code == 200
        assert "Brak wynikow" in resp.text

    async def test_search_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="ws-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/wiki/search?q=test")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Create page form (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageCreateForm:
    async def test_create_form_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wcf-auth")
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/pages/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_form_loads(self, client, db_session):
        project = await _create_project(db_session, "wcf-load")
        await _login_and_add_member(client, db_session, project, "wcf-load@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")
        assert resp.status_code == 200
        assert "Nowa strona wiki" in resp.text

    async def test_create_form_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wcf-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/wiki/pages/create")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Create page (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageCreate:
    async def test_create_page_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wc-auth")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Test", "content": "# Test", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/fake.md")
    async def test_create_page_success(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "wc-ok")
        await _login_and_add_member(client, db_session, project, "wc-ok@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Moja Strona", "content": "# Witaj", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/wiki/pages/" in resp.headers["location"]
        mock_upload.assert_called_once()

    async def test_create_page_empty_title_shows_error(self, client, db_session):
        project = await _create_project(db_session, "wc-notitle")
        await _login_and_add_member(client, db_session, project, "wc-notitle@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 200
        assert "Tytul jest wymagany" in resp.text

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/fake.md")
    async def test_create_page_with_custom_position(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "wc-pos")
        await _login_and_add_member(client, db_session, project, "wc-pos@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Strona z pozycja", "content": "Tresc", "position": "5"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        # Verify the page was created with the correct position
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Strona z pozycja"))
        page = result.scalar_one()
        assert page.position == 5

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/fake.md")
    async def test_create_page_invalid_position_defaults_to_zero(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "wc-badpos")
        await _login_and_add_member(client, db_session, project, "wc-badpos@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Strona zla pozycja", "content": "Tresc", "position": "abc"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Strona zla pozycja"))
        page = result.scalar_one()
        assert page.position == 0

    async def test_create_page_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wc-noproj@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-slug/wiki/pages/create",
            data={"title": "Test", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. Page detail (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageDetail:
    async def test_page_detail_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wpd-auth")
        # Need a user to create the page
        user = User(email="wpd-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Auth Detail")

        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc strony testowej")
    async def test_page_detail_loads(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-load")
        user = await _login_and_add_member(client, db_session, project, "wpd-load@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Strona Testowa")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert "Strona Testowa" in resp.text
        assert "Tresc strony testowej" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Parent content")
    async def test_page_detail_shows_children(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-children")
        user = await _login_and_add_member(client, db_session, project, "wpd-children@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic")
        await _create_wiki_page(db_session, project, user, title="Dziecko", parent_id=parent.id)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{parent.id}")
        assert resp.status_code == 200
        assert "Podstrony" in resp.text
        assert "Dziecko" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Content")
    async def test_page_detail_shows_breadcrumbs_for_child(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-breadcrumb")
        user = await _login_and_add_member(client, db_session, project, "wpd-breadcrumb@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Dokumentacja")
        child = await _create_wiki_page(db_session, project, user, title="Instalacja", parent_id=parent.id)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{child.id}")
        assert resp.status_code == 200
        assert "Dokumentacja" in resp.text
        assert "Instalacja" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Detail")
    async def test_page_detail_shows_edit_link(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-editlink")
        user = await _login_and_add_member(client, db_session, project, "wpd-editlink@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Edit Test")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Sub")
    async def test_page_detail_shows_add_subpage_link(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-sublink")
        user = await _login_and_add_member(client, db_session, project, "wpd-sublink@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Sub Link Test")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert f"/dashboard/{project.slug}/wiki/pages/{page.id}/create" in resp.text

    async def test_page_detail_not_found(self, client, db_session):
        project = await _create_project(db_session, "wpd-nf")
        await _login_and_add_member(client, db_session, project, "wpd-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{fake_id}")
        assert resp.status_code == 404

    async def test_page_detail_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wpd-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}")
        assert resp.status_code == 404

    @patch("monolynx.services.wiki.get_markdown", return_value="# Detail")
    async def test_page_detail_shows_author_email(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wpd-author")
        user = await _login_and_add_member(client, db_session, project, "wpd-author@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Author Page")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert "wpd-author@test.com" in resp.text


# ---------------------------------------------------------------------------
# 6. Child page create form (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiChildCreateForm:
    async def test_child_create_form_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wccf-auth")
        user = User(email="wccf-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        parent = await _create_wiki_page(db_session, project, user, title="Parent Auth")

        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_child_create_form_loads(self, client, db_session):
        project = await _create_project(db_session, "wccf-load")
        user = await _login_and_add_member(client, db_session, project, "wccf-load@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic Form")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create")
        assert resp.status_code == 200
        assert "Nowa podstrona" in resp.text
        assert "Rodzic Form" in resp.text

    async def test_child_create_form_parent_not_found(self, client, db_session):
        project = await _create_project(db_session, "wccf-nf")
        await _login_and_add_member(client, db_session, project, "wccf-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{fake_id}/create")
        assert resp.status_code == 404

    async def test_child_create_form_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wccf-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}/create")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. Create child page (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiChildCreate:
    async def test_child_create_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wcc-auth")
        user = User(email="wcc-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        parent = await _create_wiki_page(db_session, project, user, title="Parent CC Auth")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
            data={"title": "Child", "content": "Content", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/fake-child.md")
    async def test_child_create_success(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "wcc-ok")
        user = await _login_and_add_member(client, db_session, project, "wcc-ok@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Parent CC OK")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
            data={"title": "Podstrona", "content": "Tresc podstrony", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/wiki/pages/" in resp.headers["location"]

        # Verify the child page is in DB with correct parent
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Podstrona"))
        child = result.scalar_one()
        assert child.parent_id == parent.id

    async def test_child_create_empty_title_shows_error(self, client, db_session):
        project = await _create_project(db_session, "wcc-notitle")
        user = await _login_and_add_member(client, db_session, project, "wcc-notitle@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Parent CC NoTitle")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
            data={"title": "", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 200
        assert "Tytul jest wymagany" in resp.text

    async def test_child_create_parent_not_found(self, client, db_session):
        project = await _create_project(db_session, "wcc-nfparent")
        await _login_and_add_member(client, db_session, project, "wcc-nfparent@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{fake_id}/create",
            data={"title": "Dziecko", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 404

    async def test_child_create_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wcc-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}/create",
            data={"title": "Test", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 8. Edit page form (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageEditForm:
    async def test_edit_form_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wef-auth")
        user = User(email="wef-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Edit Auth Page")

        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Istniejaca tresc")
    async def test_edit_form_loads(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "wef-load")
        user = await _login_and_add_member(client, db_session, project, "wef-load@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Strona do edycji")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")
        assert resp.status_code == 200
        assert "Edytuj stronę" in resp.text
        assert "Strona do edycji" in resp.text

    async def test_edit_form_page_not_found(self, client, db_session):
        project = await _create_project(db_session, "wef-nf")
        await _login_and_add_member(client, db_session, project, "wef-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{fake_id}/edit")
        assert resp.status_code == 404

    async def test_edit_form_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wef-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}/edit")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9. Edit page (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageEdit:
    async def test_edit_page_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "we-auth")
        user = User(email="we-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Edit Auth")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Zmieniony", "content": "Nowa tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/updated.md")
    async def test_edit_page_success(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "we-ok")
        user = await _login_and_add_member(client, db_session, project, "we-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Przed edycja")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Po edycji", "content": "Nowa tresc", "position": "3"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/wiki/pages/{page.id}" in resp.headers["location"]

        await db_session.refresh(page)
        assert page.title == "Po edycji"
        assert page.position == 3
        mock_upload.assert_called_once()

    @patch("monolynx.services.wiki.get_markdown", return_value="# Old")
    async def test_edit_page_empty_title_shows_error(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "we-notitle")
        user = await _login_and_add_member(client, db_session, project, "we-notitle@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Bez tytulu")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 200
        assert "Tytul jest wymagany" in resp.text

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/updated.md")
    async def test_edit_page_keeps_slug_if_title_unchanged(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "we-sameslug")
        user = await _login_and_add_member(client, db_session, project, "we-sameslug@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Niezmienny tytul")
        old_slug = page.slug

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Niezmienny tytul", "content": "Zmieniona tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.slug == old_slug

    async def test_edit_page_not_found(self, client, db_session):
        project = await _create_project(db_session, "we-nf")
        await _login_and_add_member(client, db_session, project, "we-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{fake_id}/edit",
            data={"title": "Test", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 404

    async def test_edit_page_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="we-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}/edit",
            data={"title": "Test", "content": "Tresc", "position": "0"},
        )
        assert resp.status_code == 404

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="test-project/updated.md")
    async def test_edit_page_invalid_position_keeps_old(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "we-badpos")
        user = await _login_and_add_member(client, db_session, project, "we-badpos@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Pozycja test")
        # Set a known position
        page.position = 7
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Pozycja test", "content": "Nowa tresc", "position": "xyz"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.position == 7


# ---------------------------------------------------------------------------
# 10. Delete page (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageDelete:
    async def test_delete_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wd-auth")
        user = User(email="wd-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Delete Auth")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.wiki.delete_object")
    async def test_delete_page_success(self, mock_delete_obj, client, db_session):
        project = await _create_project(db_session, "wd-ok")
        user = await _login_and_add_member(client, db_session, project, "wd-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Usun mnie")
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/wiki/" in resp.headers["location"]
        mock_delete_obj.assert_called_once()

        # Verify page no longer in DB
        result = await db_session.execute(select(WikiPage).where(WikiPage.id == page_id))
        assert result.scalar_one_or_none() is None

    @patch("monolynx.services.wiki.delete_object")
    async def test_delete_page_with_children(self, mock_delete_obj, client, db_session):
        project = await _create_project(db_session, "wd-children")
        user = await _login_and_add_member(client, db_session, project, "wd-children@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic do usuniecia")
        child = await _create_wiki_page(db_session, project, user, title="Dziecko do usuniecia", parent_id=parent.id)
        parent_id = parent.id
        child_id = child.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{parent_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Both parent and child should be gone
        result_parent = await db_session.execute(select(WikiPage).where(WikiPage.id == parent_id))
        assert result_parent.scalar_one_or_none() is None
        result_child = await db_session.execute(select(WikiPage).where(WikiPage.id == child_id))
        assert result_child.scalar_one_or_none() is None

        # delete_object should be called for both parent and child
        assert mock_delete_obj.call_count == 2

    async def test_delete_page_not_found(self, client, db_session):
        project = await _create_project(db_session, "wd-nf")
        await _login_and_add_member(client, db_session, project, "wd-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{fake_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_delete_page_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wd-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-slug/wiki/pages/{fake_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 11. Upload image (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiUpload:
    async def test_upload_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wu-auth")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload",
            follow_redirects=False,
        )
        # Upload endpoint returns JSON 401, not redirect
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    @patch("monolynx.dashboard.wiki.upload_attachment", return_value="wu-ok/attachments/abc123.png")
    async def test_upload_success(self, mock_upload_att, client, db_session):
        project = await _create_project(db_session, "wu-ok")
        await _login_and_add_member(client, db_session, project, "wu-ok@test.com")

        file_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload",
            files={"file": ("test-image.png", file_content, "image/png")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "filePath" in data["data"]
        assert "/wiki/attachments/" in data["data"]["filePath"]
        mock_upload_att.assert_called_once()

    async def test_upload_no_file(self, client, db_session):
        project = await _create_project(db_session, "wu-nofile")
        await _login_and_add_member(client, db_session, project, "wu-nofile@test.com")

        resp = await client.post(f"/dashboard/{project.slug}/wiki/upload")
        assert resp.status_code == 400
        assert "Brak pliku" in resp.json()["error"]

    async def test_upload_too_large(self, client, db_session):
        project = await _create_project(db_session, "wu-large")
        await _login_and_add_member(client, db_session, project, "wu-large@test.com")

        # Create file larger than 200 MB
        large_data = b"\x00" * (200 * 1024 * 1024 + 1)
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload",
            files={"file": ("huge.png", large_data, "image/png")},
        )
        assert resp.status_code == 400
        assert "Plik za duzy" in resp.json()["error"]

    async def test_upload_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wu-noproj@test.com")
        file_content = b"\x89PNG\r\n\x1a\n"
        resp = await client.post(
            "/dashboard/nonexistent-slug/wiki/upload",
            files={"file": ("test.png", file_content, "image/png")},
        )
        assert resp.status_code == 404

    @patch("monolynx.dashboard.wiki.upload_attachment", return_value="wu-ext/attachments/abc123.jpg")
    async def test_upload_returns_correct_path_format(self, mock_upload_att, client, db_session):
        project = await _create_project(db_session, "wu-ext")
        await _login_and_add_member(client, db_session, project, "wu-ext@test.com")

        file_content = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload",
            files={"file": ("photo.jpg", file_content, "image/jpeg")},
        )
        assert resp.status_code == 200
        data = resp.json()
        # The path should be relative and contain the project slug
        assert f"/dashboard/{project.slug}/wiki/attachments/" in data["data"]["filePath"]


# ---------------------------------------------------------------------------
# 12. Serve attachment (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiAttachment:
    async def test_attachment_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wa-auth")
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/attachments/test.png",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.dashboard.wiki.minio_get_attachment", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    async def test_attachment_success(self, mock_get_att, client, db_session):
        project = await _create_project(db_session, "wa-ok")
        await _login_and_add_member(client, db_session, project, "wa-ok@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/attachments/abc123.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == b"\x89PNG\r\n\x1a\n"
        mock_get_att.assert_called_once_with(f"{project.slug}/attachments/abc123.png")

    @patch("monolynx.dashboard.wiki.minio_get_attachment", side_effect=Exception("Not found"))
    async def test_attachment_not_found(self, mock_get_att, client, db_session):
        project = await _create_project(db_session, "wa-nf")
        await _login_and_add_member(client, db_session, project, "wa-nf@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/attachments/nonexistent.png")
        assert resp.status_code == 404
        assert "Plik nie istnieje" in resp.text

    async def test_attachment_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="wa-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/wiki/attachments/test.png")
        assert resp.status_code == 404

    @patch("monolynx.dashboard.wiki.minio_get_attachment", return_value=(b"\xff\xd8\xff\xe0", "image/jpeg"))
    async def test_attachment_serves_jpeg(self, mock_get_att, client, db_session):
        project = await _create_project(db_session, "wa-jpeg")
        await _login_and_add_member(client, db_session, project, "wa-jpeg@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/attachments/photo.jpg")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# Additional edge cases / coverage boosters
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiServiceIntegration:
    """Tests that exercise wiki service functions through the routes for deeper coverage."""

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="wsi-dup/fake.md")
    async def test_duplicate_slug_gets_suffix(self, mock_upload, mock_embeddings, client, db_session):
        """When two pages have the same title, the second gets a slug suffix."""
        project = await _create_project(db_session, "wsi-dup")
        await _login_and_add_member(client, db_session, project, "wsi-dup@test.com")

        # Create first page
        resp1 = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Duplikat", "content": "Pierwsza", "position": "0"},
            follow_redirects=False,
        )
        assert resp1.status_code == 303

        # Create second page with the same title
        resp2 = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Duplikat", "content": "Druga", "position": "1"},
            follow_redirects=False,
        )
        assert resp2.status_code == 303

        # Both should exist with different slugs
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id).order_by(WikiPage.position))
        pages = list(result.scalars().all())
        assert len(pages) == 2
        assert pages[0].slug != pages[1].slug

    @patch("monolynx.services.wiki.delete_object")
    async def test_delete_deeply_nested_pages(self, mock_delete_obj, client, db_session):
        """Deleting a root page should cascade to grandchildren."""
        project = await _create_project(db_session, "wsi-deep")
        user = await _login_and_add_member(client, db_session, project, "wsi-deep@test.com")
        root = await _create_wiki_page(db_session, project, user, title="Root")
        child = await _create_wiki_page(db_session, project, user, title="Child", parent_id=root.id)
        grandchild = await _create_wiki_page(db_session, project, user, title="Grandchild", parent_id=child.id)
        root_id = root.id
        child_id = child.id
        grandchild_id = grandchild.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{root_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # All three should be gone
        for pid in (root_id, child_id, grandchild_id):
            result = await db_session.execute(select(WikiPage).where(WikiPage.id == pid))
            assert result.scalar_one_or_none() is None

        # delete_object called for root + child + grandchild
        assert mock_delete_obj.call_count == 3

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="wsi-edit-title/fake.md")
    async def test_edit_page_changes_slug_when_title_changes(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "wsi-slugchg")
        user = await _login_and_add_member(client, db_session, project, "wsi-slugchg@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Stary tytul")
        old_slug = page.slug

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Nowy tytul", "content": "Tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.slug != old_slug
        assert page.title == "Nowy tytul"

    @patch("monolynx.services.wiki.get_markdown", return_value="**bold** and _italic_")
    async def test_page_detail_renders_markdown(self, mock_get_md, client, db_session):
        """Ensure markdown is rendered to HTML on the detail page."""
        project = await _create_project(db_session, "wsi-render")
        user = await _login_and_add_member(client, db_session, project, "wsi-render@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Markdown Test")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        # markdown should have rendered <strong> and <em>
        assert "<strong>bold</strong>" in resp.text
        assert "<em>italic</em>" in resp.text

    @patch("monolynx.dashboard.wiki.search_wiki_pages")
    @patch("monolynx.dashboard.wiki.embeddings_enabled", return_value=True)
    async def test_search_whitespace_only_query_treated_as_empty(self, mock_enabled, mock_search, client, db_session):
        """A query of only spaces should not trigger search."""
        project = await _create_project(db_session, "wsi-wsq")
        await _login_and_add_member(client, db_session, project, "wsi-wsq@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/wiki/search?q=++++")
        assert resp.status_code == 200
        mock_search.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings", side_effect=Exception("OpenAI down"))
    @patch("monolynx.services.wiki.upload_markdown", return_value="wsi-embfail/fake.md")
    async def test_create_page_succeeds_even_if_embeddings_fail(self, mock_upload, mock_embeddings, client, db_session):
        """Embedding errors should not prevent page creation (best-effort)."""
        project = await _create_project(db_session, "wsi-embfail")
        await _login_and_add_member(client, db_session, project, "wsi-embfail@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Embedding fail test", "content": "Tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Page should still be created
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Embedding fail test"))
        page = result.scalar_one()
        assert page is not None

    @patch("monolynx.services.wiki.get_markdown", return_value="# Test AI badge")
    async def test_page_detail_shows_ai_badge(self, mock_get_md, client, db_session):
        """Pages marked as AI-touched should display the AI badge."""
        project = await _create_project(db_session, "wsi-aibadge")
        user = await _login_and_add_member(client, db_session, project, "wsi-aibadge@test.com")
        page = await _create_wiki_page(db_session, project, user, title="AI Page")
        page.is_ai_touched = True
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert "Edytowane przez AI" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Backlinks test")
    async def test_page_detail_backlinks_shown_when_llm_wiki_enabled(self, mock_get_md, client, db_session):
        """Gdy wiki_llm_enabled=True na projekcie, get_backlinks jest wywolywane."""
        project = await _create_project(db_session, "wsi-bl-on")
        project.wiki_llm_enabled = True
        await db_session.flush()

        user = await _login_and_add_member(client, db_session, project, "wsi-bl-on@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Backlinks Page On")

        # Tworzymy strone-zrodlo z backlinkiem do page
        source = await _create_wiki_page(db_session, project, user, title="Source Page On")
        from monolynx.models.wiki_backlink import WikiBacklink

        backlink = WikiBacklink(
            source_page_id=source.id,
            target_page_id=page.id,
            anchor_text="link do strony",
        )
        db_session.add(backlink)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
        assert resp.status_code == 200
        assert "Source Page On" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# No backlinks")
    async def test_page_detail_backlinks_not_fetched_when_llm_wiki_disabled(self, mock_get_md, client, db_session):
        """Gdy wiki_llm_enabled=False (domyslnie), backlinki nie sa pobierane."""
        project = await _create_project(db_session, "wsi-bl-off")
        # wiki_llm_enabled jest False domyslnie
        user = await _login_and_add_member(client, db_session, project, "wsi-bl-off@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Backlinks Page Off")

        source = await _create_wiki_page(db_session, project, user, title="Source Page Off")
        from monolynx.models.wiki_backlink import WikiBacklink

        backlink = WikiBacklink(
            source_page_id=source.id,
            target_page_id=page.id,
            anchor_text="link",
        )
        db_session.add(backlink)
        await db_session.flush()

        # Mockujemy get_backlinks zeby sprawdzic czy jest wywolywane
        with patch("monolynx.dashboard.wiki.get_backlinks") as mock_gb:
            resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")
            assert resp.status_code == 200
            mock_gb.assert_not_called()


# ---------------------------------------------------------------------------
# 13. Page attachment upload (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageAttachmentUpload:
    async def test_upload_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wpau-auth")
        user = User(email="wpau-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Att Upload Auth")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            files={"filepond": ("test.txt", b"content", "text/plain")},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "Unauthorized" in resp.json()["error"]

    async def test_upload_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wpau-noproj@test.com")
        fake_page_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj/wiki/pages/{fake_page_id}/attachments/upload",
            files={"filepond": ("test.txt", b"content", "text/plain")},
        )
        assert resp.status_code == 404
        assert "Project not found" in resp.json()["error"]

    async def test_upload_page_not_found(self, client, db_session):
        project = await _create_project(db_session, "wpau-nopage")
        await _login_and_add_member(client, db_session, project, "wpau-nopage@test.com")
        fake_page_id = uuid.uuid4()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{fake_page_id}/attachments/upload",
            files={"filepond": ("test.txt", b"content", "text/plain")},
        )
        assert resp.status_code == 404
        assert "Strona nie istnieje" in resp.json()["error"]

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_success_returns_attachment_id(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "wpau-ok")
        user = await _login_and_add_member(client, db_session, project, "wpau-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Upload OK")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            files={"filepond": ("document.pdf", b"PDF content here", "application/pdf")},
        )
        assert resp.status_code == 200
        # FilePond expects plain text UUID back
        attachment_id = resp.text.strip()
        assert len(attachment_id) == 36  # UUID format
        mock_upload.assert_called_once()

        # Verify attachment created in DB
        result = await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page.id))
        att = result.scalar_one()
        assert att.filename == "document.pdf"
        assert att.mime_type == "application/pdf"

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_filename_sanitization(self, mock_upload, client, db_session):
        """Znaki specjalne w nazwie pliku sa zastepowane podkreslnikami."""
        project = await _create_project(db_session, "wpau-san")
        user = await _login_and_add_member(client, db_session, project, "wpau-san@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Sanitize")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            files={"filepond": ("mój plik!@#.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page.id))
        att = result.scalar_one()
        # Nazwa nie powinna zawierac ! @ #
        assert "!" not in att.filename
        assert "@" not in att.filename
        assert "#" not in att.filename

    async def test_upload_too_large_returns_400(self, client, db_session):
        project = await _create_project(db_session, "wpau-large")
        user = await _login_and_add_member(client, db_session, project, "wpau-large@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Upload Large")

        large_data = b"\x00" * (200 * 1024 * 1024 + 1)
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            files={"filepond": ("huge.bin", large_data, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "Plik za duzy" in resp.json()["error"]

    @patch("monolynx.dashboard.wiki.minio_upload_object", side_effect=Exception("MinIO error"))
    async def test_upload_minio_error_returns_500(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "wpau-minio")
        user = await _login_and_add_member(client, db_session, project, "wpau-minio@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Upload MinIO Error")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            files={"filepond": ("file.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 500
        assert "Blad uploadu" in resp.json()["error"]

    async def test_upload_empty_filename_returns_400(self, client, db_session):
        project = await _create_project(db_session, "wpau-nofn")
        user = await _login_and_add_member(client, db_session, project, "wpau-nofn@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Upload NoFilename")

        # Surowy multipart z pustym filename - httpx z ("", ...) pomija naglowek filename
        # i FastAPI zwraca 422; tu wymuszamy filename="" by trafic w galaz 400 handlera
        boundary = "----monolynxtest"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="filepond"; filename=""\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "data\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/upload",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert resp.status_code == 400
        assert "Brak nazwy pliku" in resp.json()["error"]


# ---------------------------------------------------------------------------
# 14. Page attachment serve (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageAttachmentServe:
    async def test_serve_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wpas-auth")
        user = User(email="wpas-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Att Serve Auth")
        att_id = uuid.uuid4()

        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att_id}/test.pdf",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_serve_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wpas-noproj@test.com")
        fake_page_id = uuid.uuid4()
        fake_att_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/pages/{fake_page_id}/attachments/{fake_att_id}/file.pdf")
        assert resp.status_code == 404

    async def test_serve_attachment_not_found(self, client, db_session):
        project = await _create_project(db_session, "wpas-noatt")
        user = await _login_and_add_member(client, db_session, project, "wpas-noatt@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Serve NoAtt")
        fake_att_id = uuid.uuid4()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{fake_att_id}/file.pdf")
        assert resp.status_code == 404
        assert "Attachment not found" in resp.text

    @patch("monolynx.dashboard.wiki.minio_get_attachment", return_value=(b"PDF bytes", "application/pdf"))
    async def test_serve_success(self, mock_get, client, db_session):
        project = await _create_project(db_session, "wpas-ok")
        user = await _login_and_add_member(client, db_session, project, "wpas-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Serve OK")

        att = WikiAttachment(
            wiki_page_id=page.id,
            filename="report.pdf",
            storage_path=f"{project.slug}/wiki-attachments/{page.id}/report.pdf",
            mime_type="application/pdf",
            size=100,
            created_via_ai=False,
        )
        db_session.add(att)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att.id}/report.pdf")
        assert resp.status_code == 200
        assert resp.content == b"PDF bytes"
        assert "application/pdf" in resp.headers["content-type"]

    @patch("monolynx.dashboard.wiki.minio_get_attachment", side_effect=Exception("MinIO error"))
    async def test_serve_minio_error_returns_500(self, mock_get, client, db_session):
        project = await _create_project(db_session, "wpas-minio")
        user = await _login_and_add_member(client, db_session, project, "wpas-minio@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Serve MinIO Error")

        att = WikiAttachment(
            wiki_page_id=page.id,
            filename="file.txt",
            storage_path=f"{project.slug}/wiki-attachments/{page.id}/file.txt",
            mime_type="text/plain",
            size=50,
            created_via_ai=False,
        )
        db_session.add(att)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att.id}/file.txt")
        assert resp.status_code == 500
        assert "Blad pobierania pliku" in resp.text


# ---------------------------------------------------------------------------
# 15. Page attachment delete (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageAttachmentDelete:
    async def test_delete_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wpad-auth")
        user = User(email="wpad-auth-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Att Delete Auth")
        att_id = uuid.uuid4()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wpad-noproj@test.com")
        fake_page_id = uuid.uuid4()
        fake_att_id = uuid.uuid4()
        resp = await client.post(f"/dashboard/nonexistent-slug/wiki/pages/{fake_page_id}/attachments/{fake_att_id}/delete")
        assert resp.status_code == 404

    async def test_delete_attachment_not_found(self, client, db_session):
        project = await _create_project(db_session, "wpad-noatt")
        user = await _login_and_add_member(client, db_session, project, "wpad-noatt@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Delete NoAtt")
        fake_att_id = uuid.uuid4()

        resp = await client.post(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{fake_att_id}/delete")
        assert resp.status_code == 404
        assert "Attachment not found" in resp.text

    @patch("monolynx.dashboard.wiki.minio_delete_object")
    async def test_delete_success(self, mock_delete, client, db_session):
        project = await _create_project(db_session, "wpad-ok")
        user = await _login_and_add_member(client, db_session, project, "wpad-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Delete OK")

        att = WikiAttachment(
            wiki_page_id=page.id,
            filename="todelete.txt",
            storage_path=f"{project.slug}/wiki-attachments/{page.id}/todelete.txt",
            mime_type="text/plain",
            size=10,
            created_via_ai=False,
        )
        db_session.add(att)
        await db_session.flush()
        att_id = att.id

        resp = await client.post(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att_id}/delete")
        assert resp.status_code == 200

        # Attachment should be deleted from DB
        result = await db_session.execute(select(WikiAttachment).where(WikiAttachment.id == att_id))
        assert result.scalar_one_or_none() is None
        mock_delete.assert_called_once()

    @patch("monolynx.dashboard.wiki.minio_delete_object", side_effect=Exception("MinIO error"))
    async def test_delete_minio_error_still_removes_from_db(self, mock_delete, client, db_session):
        """Blad MinIO przy usuwaniu nie przerywa usuniecia z DB."""
        project = await _create_project(db_session, "wpad-minio")
        user = await _login_and_add_member(client, db_session, project, "wpad-minio@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Att Delete MinIO Error")

        att = WikiAttachment(
            wiki_page_id=page.id,
            filename="file.txt",
            storage_path=f"{project.slug}/wiki-attachments/{page.id}/file.txt",
            mime_type="text/plain",
            size=10,
            created_via_ai=False,
        )
        db_session.add(att)
        await db_session.flush()
        att_id = att.id

        resp = await client.post(f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{att_id}/delete")
        # Endpoint catches MinIO exception (pass) and still deletes from DB -> 200
        assert resp.status_code == 200

        result = await db_session.execute(select(WikiAttachment).where(WikiAttachment.id == att_id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 16. Wiki files list (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFilesList:
    async def test_files_list_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfl-auth")
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/files/",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_files_list_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfl-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/wiki/files/")
        assert resp.status_code == 404

    async def test_files_list_empty(self, client, db_session):
        project = await _create_project(db_session, "wfl-empty")
        await _login_and_add_member(client, db_session, project, "wfl-empty@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/")
        assert resp.status_code == 200

    async def test_files_list_shows_files(self, client, db_session):
        project = await _create_project(db_session, "wfl-show")
        await _login_and_add_member(client, db_session, project, "wfl-show@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="my-document.pdf",
            storage_path=f"{project.slug}/wiki-files/my-document.pdf",
            mime_type="application/pdf",
            size=1024,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/")
        assert resp.status_code == 200
        assert "my-document.pdf" in resp.text


# ---------------------------------------------------------------------------
# 17. Wiki file upload (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFileUpload:
    async def test_upload_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfu-auth")
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("test.txt", b"content", "text/plain")},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "Unauthorized" in resp.json()["error"]

    async def test_upload_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfu-noproj@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-proj/wiki/files/upload",
            files={"filepond": ("test.txt", b"content", "text/plain")},
        )
        assert resp.status_code == 404
        assert "Project not found" in resp.json()["error"]

    async def test_upload_empty_filename_returns_400(self, client, db_session):
        project = await _create_project(db_session, "wfu-nofn")
        await _login_and_add_member(client, db_session, project, "wfu-nofn@test.com")

        # Surowy multipart z pustym filename (patrz komentarz w TestWikiPageAttachmentUpload)
        boundary = "----monolynxtest"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="filepond"; filename=""\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "data\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert resp.status_code == 400
        assert "Brak nazwy pliku" in resp.json()["error"]

    async def test_upload_too_large_returns_400(self, client, db_session):
        project = await _create_project(db_session, "wfu-large")
        await _login_and_add_member(client, db_session, project, "wfu-large@test.com")

        large_data = b"\x00" * (200 * 1024 * 1024 + 1)
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("huge.bin", large_data, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "Plik za duzy" in resp.json()["error"]

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_success(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "wfu-ok")
        await _login_and_add_member(client, db_session, project, "wfu-ok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("schema.json", b'{"key": "value"}', "application/json")},
        )
        assert resp.status_code == 200
        file_id = resp.text.strip()
        assert len(file_id) == 36  # UUID
        mock_upload.assert_called_once()

        # Verify file created in DB
        result = await db_session.execute(select(WikiFile).where(WikiFile.project_id == project.id))
        wf = result.scalar_one()
        assert wf.filename == "schema.json"
        assert wf.mime_type == "application/json"

    @patch("monolynx.dashboard.wiki.minio_upload_object", side_effect=Exception("MinIO error"))
    async def test_upload_minio_error_returns_500(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "wfu-minio")
        await _login_and_add_member(client, db_session, project, "wfu-minio@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("file.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 500
        assert "Blad uploadu" in resp.json()["error"]

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_filename_sanitization(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "wfu-san")
        await _login_and_add_member(client, db_session, project, "wfu-san@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("my file!@#.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(WikiFile).where(WikiFile.project_id == project.id))
        wf = result.scalar_one()
        assert "!" not in wf.filename
        assert "@" not in wf.filename

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_empty_safe_filename_defaults_to_file(self, mock_upload, client, db_session):
        """Jesli safe_filename po sanityzacji jest pusty, uzywa 'file'."""
        project = await _create_project(db_session, "wfu-empty-safe")
        await _login_and_add_member(client, db_session, project, "wfu-empty-safe@test.com")

        # Nazwa zawiera TYLKO znaki specjalne (po sanityzacji bedzie pusta)
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/upload",
            files={"filepond": ("!!!.txt", b"data", "text/plain")},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 18. Wiki file serve (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFileServe:
    async def test_serve_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfs-auth")
        fake_file_id = uuid.uuid4()
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/test.txt",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_serve_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfs-noproj@test.com")
        fake_file_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/files/{fake_file_id}/test.txt")
        assert resp.status_code == 404

    async def test_serve_file_not_found(self, client, db_session):
        project = await _create_project(db_session, "wfs-nof")
        await _login_and_add_member(client, db_session, project, "wfs-nof@test.com")
        fake_file_id = uuid.uuid4()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/test.txt")
        assert resp.status_code == 404
        assert "File not found" in resp.text

    @patch("monolynx.dashboard.wiki.minio_get_attachment", return_value=(b"file content", "text/plain"))
    async def test_serve_success(self, mock_get, client, db_session):
        project = await _create_project(db_session, "wfs-ok")
        await _login_and_add_member(client, db_session, project, "wfs-ok@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="readme.txt",
            storage_path=f"{project.slug}/wiki-files/readme.txt",
            mime_type="text/plain",
            size=100,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/{wiki_file.id}/readme.txt")
        assert resp.status_code == 200
        assert resp.content == b"file content"
        mock_get.assert_called_once()

    @patch("monolynx.dashboard.wiki.minio_get_attachment", side_effect=Exception("MinIO error"))
    async def test_serve_minio_error_returns_500(self, mock_get, client, db_session):
        project = await _create_project(db_session, "wfs-minio")
        await _login_and_add_member(client, db_session, project, "wfs-minio@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="file.txt",
            storage_path=f"{project.slug}/wiki-files/file.txt",
            mime_type="text/plain",
            size=50,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/{wiki_file.id}/file.txt")
        assert resp.status_code == 500
        assert "Blad pobierania pliku" in resp.text


# ---------------------------------------------------------------------------
# 19. Wiki file update description (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFileUpdateDescription:
    async def test_update_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfud-auth")
        fake_file_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/description",
            data={"description": "Test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_update_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfud-noproj@test.com")
        fake_file_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-slug/wiki/files/{fake_file_id}/description",
            data={"description": "Test"},
        )
        assert resp.status_code == 404

    async def test_update_file_not_found(self, client, db_session):
        project = await _create_project(db_session, "wfud-nof")
        await _login_and_add_member(client, db_session, project, "wfud-nof@test.com")
        fake_file_id = uuid.uuid4()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/description",
            data={"description": "Test"},
        )
        assert resp.status_code == 404
        assert "File not found" in resp.text

    async def test_update_description_success(self, client, db_session):
        project = await _create_project(db_session, "wfud-ok")
        await _login_and_add_member(client, db_session, project, "wfud-ok@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="doc.pdf",
            storage_path=f"{project.slug}/wiki-files/doc.pdf",
            mime_type="application/pdf",
            size=200,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{wiki_file.id}/description",
            data={"description": "Dokumentacja projektu"},
        )
        assert resp.status_code == 200
        assert "Dokumentacja projektu" in resp.text

        # Verify DB updated
        await db_session.refresh(wiki_file)
        assert wiki_file.description == "Dokumentacja projektu"

    async def test_update_empty_description_sets_none(self, client, db_session):
        """Pusty opis ustawia description=None w bazie."""
        project = await _create_project(db_session, "wfud-empty")
        await _login_and_add_member(client, db_session, project, "wfud-empty@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="nodesc.txt",
            storage_path=f"{project.slug}/wiki-files/nodesc.txt",
            mime_type="text/plain",
            size=10,
            created_via_ai=False,
            description="Stary opis",
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{wiki_file.id}/description",
            data={"description": ""},
        )
        assert resp.status_code == 200
        # Pusta wartosc wyswietla mdash
        assert "—" in resp.text or "—" in resp.text

        await db_session.refresh(wiki_file)
        assert wiki_file.description is None


# ---------------------------------------------------------------------------
# 20. Wiki file description edit form (GET)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFileDescriptionEditForm:
    async def test_edit_form_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfdef-auth")
        fake_file_id = uuid.uuid4()
        resp = await client.get(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/description/edit",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_edit_form_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfdef-noproj@test.com")
        fake_file_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-slug/wiki/files/{fake_file_id}/description/edit")
        assert resp.status_code == 404

    async def test_edit_form_file_not_found(self, client, db_session):
        project = await _create_project(db_session, "wfdef-nof")
        await _login_and_add_member(client, db_session, project, "wfdef-nof@test.com")
        fake_file_id = uuid.uuid4()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/description/edit")
        assert resp.status_code == 404
        assert "File not found" in resp.text

    async def test_edit_form_returns_form_html(self, client, db_session):
        project = await _create_project(db_session, "wfdef-ok")
        await _login_and_add_member(client, db_session, project, "wfdef-ok@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="info.txt",
            storage_path=f"{project.slug}/wiki-files/info.txt",
            mime_type="text/plain",
            size=10,
            created_via_ai=False,
            description="Istniejacy opis",
        )
        db_session.add(wiki_file)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/wiki/files/{wiki_file.id}/description/edit")
        assert resp.status_code == 200
        # Formularz HTMX powinien byc zwrocony
        assert "<form" in resp.text
        assert "Istniejacy opis" in resp.text


# ---------------------------------------------------------------------------
# 21. Wiki file delete (POST)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiFileDelete:
    async def test_delete_requires_auth(self, client, db_session):
        project = await _create_project(db_session, "wfd-auth")
        fake_file_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wfd-noproj@test.com")
        fake_file_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-slug/wiki/files/{fake_file_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_delete_file_not_found(self, client, db_session):
        project = await _create_project(db_session, "wfd-nof")
        await _login_and_add_member(client, db_session, project, "wfd-nof@test.com")
        fake_file_id = uuid.uuid4()

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{fake_file_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404
        assert "File not found" in resp.text

    @patch("monolynx.dashboard.wiki.minio_delete_object")
    async def test_delete_success(self, mock_delete, client, db_session):
        project = await _create_project(db_session, "wfd-ok")
        await _login_and_add_member(client, db_session, project, "wfd-ok@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="todelete.pdf",
            storage_path=f"{project.slug}/wiki-files/todelete.pdf",
            mime_type="application/pdf",
            size=100,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()
        file_id = wiki_file.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{file_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/wiki/files/" in resp.headers["location"]
        mock_delete.assert_called_once()

        result = await db_session.execute(select(WikiFile).where(WikiFile.id == file_id))
        assert result.scalar_one_or_none() is None

    @patch("monolynx.dashboard.wiki.minio_delete_object", side_effect=Exception("MinIO error"))
    async def test_delete_minio_error_still_removes_from_db(self, mock_delete, client, db_session):
        """Blad MinIO przy usuwaniu nie przerywa usuniecia z DB (endpoint lapie wyjatek)."""
        project = await _create_project(db_session, "wfd-minio")
        await _login_and_add_member(client, db_session, project, "wfd-minio@test.com")

        wiki_file = WikiFile(
            project_id=project.id,
            filename="del-minio.txt",
            storage_path=f"{project.slug}/wiki-files/del-minio.txt",
            mime_type="text/plain",
            size=10,
            created_via_ai=False,
        )
        db_session.add(wiki_file)
        await db_session.flush()
        file_id = wiki_file.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/files/{file_id}/delete",
            follow_redirects=False,
        )
        # MinIO error jest lapany (pass), wiec redirect powinien nastapic
        assert resp.status_code == 303

        result = await db_session.execute(select(WikiFile).where(WikiFile.id == file_id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# MON-203: przelacznik publikacji (is_public) w formularzu wiki
#
# Zanim implementacja powstanie (pole is_public w page_form.html, obsluga
# form.get("is_public") is not None w dashboard/wiki.py), wszystkie testy w tej
# klasie musza zawodzic -- to jest zamierzone, TDD.
# ---------------------------------------------------------------------------


def _extract_is_public_input(html: str) -> str | None:
    """Wyciaga caly tag <input ... name="is_public" ...> z HTML (niezaleznie od
    kolejnosci atrybutow), zeby sprawdzic obecnosc/stan atrybutu checked."""
    match = re.search(r'<input[^>]*name="is_public"[^>]*>', html, re.IGNORECASE)
    return match.group(0) if match else None


@pytest.mark.integration
class TestWikiPageIsPublicCheckbox:
    """Kryterium 1 (MON-203): checkbox is_public w formularzu tworzenia/edycji ustawia
    i zdejmuje flage, a stan jest widoczny po ponownym otwarciu formularza edycji."""

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon203-create/fake.md")
    async def test_create_page_with_is_public_checked_sets_public(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "mon203-create")
        await _login_and_add_member(client, db_session, project, "mon203-create@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Strona Publiczna MON203", "content": "# Tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Strona Publiczna MON203"))
        page = result.scalar_one()
        assert page.is_public is True

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon203-nocheck/fake.md")
    async def test_create_page_without_is_public_key_stays_private(self, mock_upload, mock_embeddings, client, db_session):
        """Niezaznaczony checkbox HTML nie wysyla klucza is_public w ogole -- strona ma
        zostac prywatna (pulapka z briefu Team Managera dla tego kryterium)."""
        project = await _create_project(db_session, "mon203-nocheck")
        await _login_and_add_member(client, db_session, project, "mon203-nocheck@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Strona Prywatna MON203", "content": "# Tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Strona Prywatna MON203"))
        page = result.scalar_one()
        assert page.is_public is False

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon203-edit-on/fake.md")
    async def test_edit_page_checking_is_public_makes_page_public(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "mon203-edit-on")
        user = await _login_and_add_member(client, db_session, project, "mon203-edit-on@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Do Publikacji MON203", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Do Publikacji MON203", "content": "Nowa tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.is_public is True

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon203-edit-off/fake.md")
    async def test_edit_page_unchecking_is_public_makes_page_private(self, mock_upload, mock_embeddings, client, db_session):
        """Kontrakt: brak klucza is_public w POST edit = strona prywatna, nawet gdy
        wczesniej byla publiczna (odznaczenie checkboxa)."""
        project = await _create_project(db_session, "mon203-edit-off")
        user = await _login_and_add_member(client, db_session, project, "mon203-edit-off@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Do Prywatyzacji MON203", is_public=True)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Do Prywatyzacji MON203", "content": "Nowa tresc", "position": "0"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.is_public is False

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_shows_checked_checkbox_for_public_page(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "mon203-form-pub")
        user = await _login_and_add_member(client, db_session, project, "mon203-form-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Formularz MON203", is_public=True)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")
        assert resp.status_code == 200
        tag = _extract_is_public_input(resp.text)
        assert tag is not None, "Formularz edycji nie zawiera pola is_public"
        assert "checked" in tag.lower()

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_shows_unchecked_checkbox_for_private_page(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "mon203-form-priv")
        user = await _login_and_add_member(client, db_session, project, "mon203-form-priv@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Prywatna Formularz MON203", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")
        assert resp.status_code == 200
        tag = _extract_is_public_input(resp.text)
        assert tag is not None, "Formularz edycji nie zawiera pola is_public"
        assert "checked" not in tag.lower()


# ---------------------------------------------------------------------------
# MON-203: kolizja slugu publicznego przy zapisie z UI -- flash, nie 500
#
# services/wiki.py (MON-201) juz mapuje IntegrityError partial-unique-index
# (uq_wiki_public_slug) na ValueError czytelny dla czlowieka. Te testy sprawdzaja,
# ze warstwa dashboard/wiki.py go LAPIE i pokazuje flash, a nie przepuszcza go jako
# nieobsluzony wyjatek (co dzis konczy sie 500 / propagacja wyjatku do klienta testowego).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPagePublicSlugConflictFlash:
    """Kryterium 2 (MON-203)."""

    @patch("monolynx.services.wiki.sync_backlinks")
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_create_page_public_slug_conflict_does_not_return_500(self, mock_upload, mock_embeddings, mock_sync, client, db_session):
        mock_upload.side_effect = lambda project_slug, page_id, content: f"{project_slug}/{page_id}.md"

        project_a = await _create_project(db_session, "mon203-conf-a")
        project_b = await _create_project(db_session, "mon203-conf-b")
        user_a = User(email="mon203-conf-a-setup@test.com", password_hash=hash_password("testpass123"), can_edit_blog=True)
        db_session.add(user_a)
        await db_session.flush()

        from monolynx.services.wiki import create_wiki_page as svc_create_wiki_page

        await svc_create_wiki_page(
            project_id=project_a.id,
            project_slug=project_a.slug,
            title="Konflikt Slug Flash MON203",
            content="# A",
            user_id=user_a.id,
            is_public=True,
            db=db_session,
        )

        await _login_and_add_member(client, db_session, project_b, "mon203-conf-b@test.com")
        resp = await client.post(
            f"/dashboard/{project_b.slug}/wiki/pages/create",
            data={"title": "Konflikt Slug Flash MON203", "content": "# B", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        # dashboard/wiki.py lapie ValueError i re-renderuje TemplateResponse (status 200
        # domyslny, brak redirectu -- to NIE happy-path 303). Komunikat trafia do sesji przez
        # flash(request, str(exc), "error") i jest renderowany w TEJ SAME odpowiedzi przez
        # components/toasts.html (wlaczony w layouts/base.html, ktory rozszerza page_form.html
        # -- pop z sesji dzieje sie w tym samym request/response, bez przekierowania).
        # Asercja na sam kod (200/303) nie odrozniby tej sciezki od happy-path i przezylaby
        # mutanta usuwajacego wywolanie flash() -- stad wymog dokladnego statusu i tresci.
        assert resp.status_code == 200
        assert "już istnieje w innym projekcie" in resp.text

    @patch("monolynx.services.wiki.sync_backlinks")
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_edit_page_public_slug_conflict_does_not_return_500(self, mock_upload, mock_embeddings, mock_sync, client, db_session):
        mock_upload.side_effect = lambda project_slug, page_id, content: f"{project_slug}/{page_id}.md"

        project_a = await _create_project(db_session, "mon203-confe-a")
        project_b = await _create_project(db_session, "mon203-confe-b")
        user_a = User(email="mon203-confe-a-setup@test.com", password_hash=hash_password("testpass123"), can_edit_blog=True)
        db_session.add(user_a)
        await db_session.flush()

        from monolynx.services.wiki import create_wiki_page as svc_create_wiki_page

        await svc_create_wiki_page(
            project_id=project_a.id,
            project_slug=project_a.slug,
            title="Konflikt Slug Edit MON203",
            content="# A",
            user_id=user_a.id,
            is_public=True,
            db=db_session,
        )

        user_b = await _login_and_add_member(client, db_session, project_b, "mon203-confe-b@test.com")
        # Prywatna strona w project_b powstaje z tym samym slugiem (dozwolone -- slug jest
        # unikalny tylko per-projekt), a proba oznaczenia jej jako publiczna koliduje.
        private_page = await _create_wiki_page(db_session, project_b, user_b, title="Konflikt Slug Edit MON203", is_public=False)

        resp = await client.post(
            f"/dashboard/{project_b.slug}/wiki/pages/{private_page.id}/edit",
            data={"title": "Konflikt Slug Edit MON203", "content": "Nowa tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        # Ta sama sciezka co w tescie create powyzej -- 200 (re-render, nie redirect) +
        # komunikat z flash() faktycznie widoczny w HTML tej samej odpowiedzi.
        assert resp.status_code == 200
        assert "już istnieje w innym projekcie" in resp.text


# ---------------------------------------------------------------------------
# MON-264: metadane wpisu, podglad szkicu, menu blokow i upload obrazkow w edytorze
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _anonymous_client() -> httpx.AsyncClient:
    """Klient bez ciasteczek sesji (osobny od `client` z zalogowanym uzytkownikiem); `get_db` jest juz nadpisany przez fixture `client`."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login_regular_member(client, db_session, project, email, permissions=None):
    """Zwykly (nie-superuser) czlonek projektu. `permissions` podane = rola z RBAC o dokladnie tych uprawnieniach."""
    await login_session(client, db_session, email=email, is_superuser=False)
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    role_id = None
    if permissions is not None:
        role = Role(name=f"rola-{uuid.uuid4().hex[:8]}", project_id=project.id, permissions=permissions, is_system=False)
        db_session.add(role)
        await db_session.flush()
        role_id = role.id
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="member", role_id=role_id))
    await db_session.flush()
    return user


async def _add_blog_post(db_session, page, slug=None, **fields):
    """BlogPost dla strony; published_at ustawione tylko dla strony publicznej."""
    fields.setdefault("published_at", datetime.now(UTC) if page.is_public else None)
    post = BlogPost(page_id=page.id, slug=slug or page.slug, **fields)
    db_session.add(post)
    await db_session.flush()
    return post


async def _add_attachment(db_session, page, filename="obraz.png", mime_type="image/png"):
    attachment = WikiAttachment(
        wiki_page_id=page.id,
        filename=filename,
        storage_path=f"wiki-attachments/{page.id}/{filename}",
        mime_type=mime_type,
        size=10,
        created_via_ai=False,
    )
    db_session.add(attachment)
    await db_session.flush()
    return attachment


async def _blog_post_of(db_session, page_id):
    return (await db_session.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one_or_none()


def _default_blog_form(page, **overrides):
    """Pola sekcji "Wpis na blogu" w stanie, w jakim formularz wysyla ja nietknieta dla strony bez BlogPost."""
    form = {
        "title": page.title,
        "content": "Tresc strony",
        "position": "0",
        "blog_meta": "1",
        "blog_slug": page.slug,
        "blog_description": "",
        "blog_author_name": DEFAULT_AUTHOR_NAME,
        "blog_tags": "",
        "blog_lang": "pl",
        "blog_translation_of_id": "",
        "blog_cover_attachment_id": "",
        "blog_last_verified_at": "",
    }
    form.update(overrides)
    return form


def _tag(html: str, name: str) -> str | None:
    match = re.search(rf'<(?:input|select)[^>]*name="{name}"[^>]*>', html, re.IGNORECASE)
    return match.group(0) if match else None


def _textarea(html: str, name: str) -> str | None:
    match = re.search(rf'<textarea[^>]*name="{name}"[^>]*>(.*?)</textarea>', html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def _options(html: str, name: str) -> list[tuple[str, bool]]:
    """(value, selected) kazdej opcji selecta o danej nazwie, w kolejnosci z HTML."""
    block = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', html, re.IGNORECASE | re.DOTALL)
    assert block is not None, f"brak selecta {name}"
    result = []
    for attrs in re.findall(r"<option([^>]*)>", block.group(1), re.IGNORECASE):
        value = re.search(r'value="([^"]*)"', attrs)
        result.append((value.group(1) if value else "", re.search(r"\bselected\b", attrs) is not None))
    return result


def _blog_meta_details_tag(html: str) -> str | None:
    match = re.search(r'<details[^>]*id="blog-meta-section"[^>]*>', html, re.IGNORECASE)
    return match.group(0) if match else None


def _block_templates_json(html: str) -> list[dict[str, str]]:
    match = re.search(r'<script[^>]*type="application/json"[^>]*id="blog-block-templates"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert match is not None, "brak <script id=blog-block-templates>"
    data: list[dict[str, str]] = json.loads(match.group(1))
    return data


@pytest.mark.integration
class TestWikiPageBlogMetaSave:
    """Kryterium 1: zapis formularza ustawia metadane wpisu; blad walidacji = flash + wpisane wartosci, tresc niezapisana."""

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-save/fake.md")
    async def test_edit_saves_every_blog_meta_field(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-save")
        user = await _login_and_add_member(client, db_session, project, "m264-save@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Do Metadanych", is_public=True)
        await _add_blog_post(db_session, page, slug="stary-adres-m264")
        original = await _create_wiki_page(db_session, project, user, title="Oryginal Wpisu", is_public=True)
        await _add_blog_post(db_session, original, slug="oryginal-m264")
        cover = await _add_attachment(db_session, page)
        page_id, original_id, cover_id = page.id, original.id, cover.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(
                page,
                is_public="on",
                blog_slug="nowy-adres-m264",
                blog_description="Krotki opis wpisu",
                blog_author_name="Anna Autorka",
                blog_tags="alfa, beta ,, gamma",
                blog_lang="en",
                blog_translation_of_id=str(original_id),
                blog_cover_attachment_id=str(cover_id),
                blog_last_verified_at="2026-05-04",
            ),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.slug == "nowy-adres-m264"
        assert post.description == "Krotki opis wpisu"
        assert post.author_name == "Anna Autorka"
        assert post.tags == ["alfa", "beta", "gamma"]
        assert post.lang == "en"
        assert post.translation_of_id == original_id
        assert post.cover_attachment_id == cover_id
        assert post.last_verified_at == date(2026, 5, 4)

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-clear/fake.md")
    async def test_edit_with_blank_nullable_fields_clears_them(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-clear")
        user = await _login_and_add_member(client, db_session, project, "m264-clear@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Do Czyszczenia", is_public=True)
        original = await _create_wiki_page(db_session, project, user, title="Oryginal Do Czyszczenia", is_public=True)
        await _add_blog_post(db_session, original, slug="oryginal-czysc-m264")
        cover = await _add_attachment(db_session, page)
        await _add_blog_post(
            db_session,
            page,
            slug="wpis-czysc-m264",
            description="Stary opis",
            translation_of_id=original.id,
            cover_attachment_id=cover.id,
            last_verified_at=date(2026, 1, 2),
        )
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, is_public="on", blog_slug="wpis-czysc-m264"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.description == ""
        assert post.translation_of_id is None
        assert post.cover_attachment_id is None
        assert post.last_verified_at is None

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-blank/fake.md")
    async def test_edit_with_blank_slug_and_author_keeps_stored_values(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-blank")
        user = await _login_and_add_member(client, db_session, project, "m264-blank@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Z Zachowanymi", is_public=True)
        await _add_blog_post(db_session, page, slug="zostaje-m264", author_name="Zostaje Autor")
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, is_public="on", blog_slug="", blog_author_name="", blog_description="Nowy opis"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.slug == "zostaje-m264"
        assert post.author_name == "Zostaje Autor"
        assert post.description == "Nowy opis"

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-draft/fake.md")
    async def test_edit_saves_blog_meta_of_unpublished_page_that_has_a_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-draft")
        user = await _login_and_add_member(client, db_session, project, "m264-draft@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Szkic Z Wpisem", is_public=False)
        await _add_blog_post(db_session, page, slug="szkic-m264")
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, blog_slug="szkic-m264", blog_description="Opis szkicu"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.description == "Opis szkicu"
        await db_session.refresh(page)
        assert page.is_public is False

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-plain/fake.md")
    async def test_plain_page_with_untouched_blog_section_gets_no_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-plain")
        user = await _login_and_add_member(client, db_session, project, "m264-plain@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Zwykla Strona Wiki", is_public=False)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, title="Zwykla Strona Wiki Po Edycji"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        await db_session.refresh(page)
        assert page.title == "Zwykla Strona Wiki Po Edycji"
        assert await _blog_post_of(db_session, page_id) is None

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-fill/fake.md")
    async def test_plain_page_with_filled_blog_section_gets_a_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-fill")
        user = await _login_and_add_member(client, db_session, project, "m264-fill@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Zwykla Z Opisem", is_public=False)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, blog_description="Opis wpisany od zera"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.description == "Opis wpisany od zera"
        await db_session.refresh(page)
        assert page.is_public is False

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-nomarker/fake.md")
    async def test_blog_fields_without_marker_are_ignored(self, mock_upload, mock_embeddings, client, db_session):
        """Formularz bez sekcji (brak pola `blog_meta`) nie zapisuje metadanych, nawet gdy pola `blog_*` przyszly w zadaniu."""
        project = await _create_project(db_session, "m264-nomarker")
        user = await _login_and_add_member(client, db_session, project, "m264-nomarker@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Bez Znacznika", is_public=False)
        form = _default_blog_form(page, blog_description="Opis bez znacznika")
        del form["blog_meta"]
        page_id = page.id

        resp = await client.post(f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit", data=form, follow_redirects=False)

        assert resp.status_code == 303
        assert await _blog_post_of(db_session, page_id) is None

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-err/fake.md")
    async def test_too_long_description_flashes_error_keeps_typed_values_and_saves_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-err")
        user = await _login_and_add_member(client, db_session, project, "m264-err@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Z Bledem", is_public=False)
        long_description = "Opis " + "a" * 300

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(
                page,
                title="Nowy tytul wpisany MON264",
                content="Tresc wpisana MON264",
                blog_description=long_description,
                blog_author_name="Autorka Z Bledem",
                blog_tags="alfa, beta",
            ),
        )

        assert resp.status_code == 200
        assert "Opis może mieć maksymalnie 300 znaków" in resp.text
        assert long_description in resp.text
        assert "Autorka Z Bledem" in resp.text
        assert "alfa, beta" in resp.text
        assert "Nowy tytul wpisany MON264" in resp.text
        assert "Tresc wpisana MON264" in resp.text
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-lang/fake.md")
    async def test_unsupported_language_flashes_error_and_saves_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-lang")
        user = await _login_and_add_member(client, db_session, project, "m264-lang@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Zly Jezyk", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(page, content="Tresc zlego jezyka MON264", blog_lang="de"),
        )

        assert resp.status_code == 200
        assert "Język musi być jednym z: pl, en" in resp.text
        assert "Tresc zlego jezyka MON264" in resp.text
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m272-panel-lang/fake.md")
    async def test_mon272_panel_changing_lang_of_original_to_language_of_its_translation_is_refused(
        self, mock_upload, mock_embeddings, client, db_session
    ):
        """MON-272 pkt 7: formularz oryginału z `blog_lang` języka jego tłumaczenia i pustym oryginałem daje błąd, a język w bazie zostaje."""
        project = await _create_project(db_session, "m272-panel-lang")
        user = await _login_and_add_member(client, db_session, project, "m272-panel-lang@test.com")
        original = await _create_wiki_page(db_session, project, user, title="Oryginal Jezyk M272", is_public=False)
        await _add_blog_post(db_session, original, slug="oryginal-jezyk-m272", lang="pl")
        translation = await _create_wiki_page(db_session, project, user, title="Tlumaczenie Jezyk M272", is_public=False)
        await _add_blog_post(db_session, translation, slug="tlumaczenie-jezyk-m272", lang="en", translation_of_id=original.id)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{original.id}/edit",
            data=_default_blog_form(original, blog_slug="oryginal-jezyk-m272", blog_lang="en", blog_translation_of_id=""),
        )

        assert resp.status_code == 200
        assert "Tłumaczenie musi być w innym języku niż oryginał" in resp.text
        post = await _blog_post_of(db_session, original.id)
        assert post is not None
        assert post.lang == "pl"
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m272-panel-lang-ok/fake.md")
    async def test_mon272_panel_changing_lang_of_unlinked_post_is_saved(self, mock_upload, mock_embeddings, client, db_session):
        """MON-272 pkt 7 (para pozytywna): formularz wpisu bez powiązań z nowym `blog_lang` i pustym oryginałem zapisuje język."""
        project = await _create_project(db_session, "m272-panel-lang-ok")
        user = await _login_and_add_member(client, db_session, project, "m272-panel-lang-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Bez Powiazan M272", is_public=False)
        await _add_blog_post(db_session, page, slug="wpis-bez-powiazan-m272", lang="pl")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(page, blog_slug="wpis-bez-powiazan-m272", blog_lang="en", blog_translation_of_id=""),
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page.id)
        assert post is not None
        assert post.lang == "en"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("blog_last_verified_at", "2026-13-45"),
            ("blog_translation_of_id", "to-nie-jest-uuid"),
            ("blog_cover_attachment_id", "to-nie-jest-uuid"),
        ],
    )
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-parse/fake.md")
    async def test_unparsable_value_is_a_validation_error_not_a_server_error(self, mock_upload, mock_embeddings, field, value, client, db_session):
        project = await _create_project(db_session, f"m264-parse-{field.replace('_', '-')}")
        user = await _login_and_add_member(client, db_session, project, f"m264-parse-{field}@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Zla Wartosc", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(page, content="Tresc zlej wartosci MON264", **{field: value}),
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert "Tresc zlej wartosci MON264" in resp.text
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-date/fake.md")
    async def test_unparsable_verification_date_is_kept_in_the_date_input(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m264-date")
        user = await _login_and_add_member(client, db_session, project, "m264-date@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Zla Data", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(page, blog_last_verified_at="2026-13-45"),
        )

        assert resp.status_code == 200
        assert 'value="2026-13-45"' in (_tag(resp.text, "blog_last_verified_at") or "")

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("blog_translation_of_id", "to-nie-jest-uuid", "Nieprawidłowy identyfikator tłumaczenia"),
            ("blog_cover_attachment_id", "to-nie-jest-uuid", "Nieprawidłowy identyfikator okładki"),
            ("blog_last_verified_at", "2026-13-45", "Data weryfikacji ma nieprawidłowy format (RRRR-MM-DD)"),
        ],
    )
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-msg/fake.md")
    async def test_unparsable_value_flashes_the_message_of_its_own_field(
        self, mock_upload, mock_embeddings, field, value, message, client, db_session
    ):
        suffix = field.removeprefix("blog_").replace("_", "-")
        project = await _create_project(db_session, f"m264-msg-{suffix}")
        user = await _login_and_add_member(client, db_session, project, f"m264-msg-{suffix}@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Komunikat Pola", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data=_default_blog_form(page, **{field: value}),
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert message in resp.text
        mock_upload.assert_not_called()

    @pytest.mark.parametrize(
        ("case", "overrides", "field", "expected"),
        [
            ("slug", {"blog_slug": "tylko-nowy-adres-m264"}, "slug", "tylko-nowy-adres-m264"),
            ("author", {"blog_author_name": "Tylko Inny Autor"}, "author_name", "Tylko Inny Autor"),
            ("tags", {"blog_tags": "jedyny-tag"}, "tags", ["jedyny-tag"]),
        ],
    )
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-touch/fake.md")
    async def test_plain_page_with_only_one_changed_blog_field_gets_a_blog_post(
        self, mock_upload, mock_embeddings, case, overrides, field, expected, client, db_session
    ):
        project = await _create_project(db_session, f"m264-touch-{case}")
        user = await _login_and_add_member(client, db_session, project, f"m264-touch-{case}@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Zwykla Strona Jedno Pole", is_public=False)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, **overrides),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert getattr(post, field) == expected


@pytest.mark.integration
class TestWikiPageFormBlogContext:
    """Formularz edycji: sekcja "Wpis na blogu", select tlumaczenia i okladki, adres uploadu."""

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_of_a_post_has_open_section_filled_from_the_post(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-post")
        user = await _login_and_add_member(client, db_session, project, "m264-form-post@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis W Formularzu", is_public=True)
        cover = await _add_attachment(db_session, page)
        await _add_blog_post(
            db_session,
            page,
            slug="adres-w-formularzu-m264",
            description="Opis z bazy MON264",
            author_name="Autor Z Bazy",
            tags=["alfa", "beta"],
            lang="en",
            cover_attachment_id=cover.id,
            last_verified_at=date(2026, 5, 4),
        )

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert resp.status_code == 200
        details = _blog_meta_details_tag(resp.text)
        assert details is not None
        assert re.search(r"\bopen\b", details)
        assert 'name="blog_meta"' in resp.text
        assert 'value="adres-w-formularzu-m264"' in (_tag(resp.text, "blog_slug") or "")
        assert _textarea(resp.text, "blog_description") == "Opis z bazy MON264"
        assert 'value="Autor Z Bazy"' in (_tag(resp.text, "blog_author_name") or "")
        assert 'value="alfa, beta"' in (_tag(resp.text, "blog_tags") or "")
        assert 'value="2026-05-04"' in (_tag(resp.text, "blog_last_verified_at") or "")
        assert [value for value, selected in _options(resp.text, "blog_lang") if selected] == ["en"]
        assert [value for value, selected in _options(resp.text, "blog_cover_attachment_id") if selected] == [str(cover.id)]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_of_a_plain_page_has_collapsed_section_with_defaults(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-plain")
        user = await _login_and_add_member(client, db_session, project, "m264-form-plain@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Zwykla W Formularzu", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert resp.status_code == 200
        details = _blog_meta_details_tag(resp.text)
        assert details is not None
        assert not re.search(r"\bopen\b", details)
        assert f'value="{page.slug}"' in (_tag(resp.text, "blog_slug") or "")
        assert f'value="{DEFAULT_AUTHOR_NAME}"' in (_tag(resp.text, "blog_author_name") or "")
        assert [value for value, selected in _options(resp.text, "blog_lang") if selected] == ["pl"]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_of_a_public_page_without_post_row_has_open_section(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-pub")
        user = await _login_and_add_member(client, db_session, project, "m264-form-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Bez Wiersza", is_public=True)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        details = _blog_meta_details_tag(resp.text)
        assert details is not None
        assert re.search(r"\bopen\b", details)

    async def test_create_forms_have_no_blog_section(self, client, db_session):
        project = await _create_project(db_session, "m264-form-create")
        user = await _login_and_add_member(client, db_session, project, "m264-form-create@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic Formularza")

        top = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")
        child = await client.get(f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create")

        assert top.status_code == 200
        assert child.status_code == 200
        for resp in (top, child):
            assert "blog-meta-section" not in resp.text
            assert 'name="blog_meta"' not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_translation_select_lists_only_other_pages_that_are_posts(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-trans")
        user = await _login_and_add_member(client, db_session, project, "m264-form-trans@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Edytowany Wpis", is_public=True)
        await _add_blog_post(db_session, page, slug="edytowany-m264")
        other_post = await _create_wiki_page(db_session, project, user, title="Inny Wpis", is_public=True)
        await _add_blog_post(db_session, other_post, slug="inny-m264")
        plain = await _create_wiki_page(db_session, project, user, title="Zwykla Strona Obok", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        values = [value for value, _ in _options(resp.text, "blog_translation_of_id")]
        assert str(other_post.id) in values
        assert str(page.id) not in values
        assert str(plain.id) not in values
        assert "" in values

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_translation_select_excludes_posts_of_another_project(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-trans-scope")
        foreign_project = await _create_project(db_session, "m264-trans-foreign")
        user = await _login_and_add_member(client, db_session, project, "m264-trans-scope@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis W Swoim Projekcie", is_public=True)
        await _add_blog_post(db_session, page, slug="swoj-wpis-m264")
        own_other = await _create_wiki_page(db_session, project, user, title="Drugi Wpis Swojego Projektu", is_public=True)
        await _add_blog_post(db_session, own_other, slug="swoj-drugi-m264")
        foreign = await _create_wiki_page(db_session, foreign_project, user, title="Wpis Obcego Projektu", is_public=True)
        await _add_blog_post(db_session, foreign, slug="obcy-wpis-m264")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        values = [value for value, _ in _options(resp.text, "blog_translation_of_id")]
        assert str(own_other.id) in values
        assert str(foreign.id) not in values
        assert "Wpis Obcego Projektu" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_translation_select_orders_options_by_title(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-trans-order")
        user = await _login_and_add_member(client, db_session, project, "m264-trans-order@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Edytowany Dla Kolejnosci", is_public=True)
        await _add_blog_post(db_session, page, slug="edytowany-kolejnosc-m264")
        third = await _create_wiki_page(db_session, project, user, title="Ccc Trzeci Tytul", is_public=True)
        await _add_blog_post(db_session, third, slug="ccc-m264")
        first = await _create_wiki_page(db_session, project, user, title="Aaa Pierwszy Tytul", is_public=True)
        await _add_blog_post(db_session, first, slug="aaa-m264")
        second = await _create_wiki_page(db_session, project, user, title="Bbb Drugi Tytul", is_public=True)
        await _add_blog_post(db_session, second, slug="bbb-m264")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        values = [value for value, _ in _options(resp.text, "blog_translation_of_id") if value]
        assert values == [str(first.id), str(second.id), str(third.id)]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_cover_select_lists_only_attachments_of_this_page(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-cover")
        user = await _login_and_add_member(client, db_session, project, "m264-form-cover@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Strona Z Okladka", is_public=True)
        other = await _create_wiki_page(db_session, project, user, title="Strona Obok Okladki", is_public=True)
        own_attachment = await _add_attachment(db_session, page, filename="moja.png")
        foreign_attachment = await _add_attachment(db_session, other, filename="cudza.png")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        values = [value for value, _ in _options(resp.text, "blog_cover_attachment_id")]
        assert str(own_attachment.id) in values
        assert str(foreign_attachment.id) not in values
        assert "" in values

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_points_the_editor_upload_at_this_page(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-form-upl")
        user = await _login_and_add_member(client, db_session, project, "m264-form-upl@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Z Edycji")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert f"/dashboard/{project.slug}/wiki/upload?page_id={page.id}" in resp.text

    async def test_create_form_keeps_the_upload_endpoint_without_page_id(self, client, db_session):
        project = await _create_project(db_session, "m264-form-upl2")
        await _login_and_add_member(client, db_session, project, "m264-form-upl2@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")

        assert f"/dashboard/{project.slug}/wiki/upload" in resp.text
        assert "wiki/upload?page_id=" not in resp.text


@pytest.mark.integration
class TestWikiBlockInsertMenu:
    """Kryterium 4: menu "Wstaw blok" korzysta z listy generowanej z rejestru, nie z listy wpisanej w szablon."""

    @staticmethod
    def _expected():
        return [{"name": name, "template": definition.syntax_template} for name, definition in [*TEXT_BLOCKS.items(), *CONTAINER_BLOCKS.items()]]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_lists_every_registered_block_with_its_template(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-menu-edit")
        user = await _login_and_add_member(client, db_session, project, "m264-menu-edit@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Menu Blokow Edycja")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert _block_templates_json(resp.text) == self._expected()

    async def test_create_forms_list_every_registered_block_with_its_template(self, client, db_session):
        project = await _create_project(db_session, "m264-menu-create")
        user = await _login_and_add_member(client, db_session, project, "m264-menu-create@test.com")
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic Menu Blokow")

        top = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")
        child = await client.get(f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create")

        assert _block_templates_json(top.text) == self._expected()
        assert _block_templates_json(child.text) == self._expected()

    async def test_block_added_to_the_registry_shows_up_in_the_form(self, client, db_session):
        project = await _create_project(db_session, "m264-menu-live")
        await _login_and_add_member(client, db_session, project, "m264-menu-live@test.com")

        def render(*args, **kwargs):
            return ""

        probe = BlockDefinition(name="zz_probe", to_html=render, to_markdown=render, syntax_template=":::zz_probe\nProbe MON264\n:::")
        with patch.dict(CONTAINER_BLOCKS, {"zz_probe": probe}):
            resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")

        listed = _block_templates_json(resp.text)
        assert listed[-1] == {"name": "zz_probe", "template": ":::zz_probe\nProbe MON264\n:::"}
        assert [item["name"] for item in listed[:-1]] == [*TEXT_BLOCKS, *CONTAINER_BLOCKS]


@pytest.mark.integration
class TestWikiBlogPreview:
    """Kryterium 2: podglad szkicu pod sesja i `wiki:read`, z `X-Robots-Tag: noindex`, bez zapisu do bazy."""

    @patch("monolynx.services.wiki.get_markdown", return_value="# Szkic wpisu\n\nTresc szkicu MON264")
    async def test_private_page_preview_returns_draft_page_with_noindex(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-ok")
        user = await _login_regular_member(client, db_session, project, "m264-prev-ok@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Prywatny Szkic", is_public=False)
        back_url = f"/dashboard/{project.slug}/wiki/pages/{page.id}"

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 200
        assert resp.headers.get("x-robots-tag") == "noindex"
        assert '<meta name="robots" content="noindex">' in resp.text
        assert f'<a href="{back_url}"' in resp.text
        assert "Podgląd szkicu" in resp.text
        assert "Tresc szkicu MON264" in resp.text
        assert 'rel="canonical"' not in resp.text
        assert "application/ld+json" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Szkic\n\nTresc")
    async def test_preview_does_not_create_a_blog_post_row(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-nowrite")
        user = await _login_regular_member(client, db_session, project, "m264-prev-nowrite@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Podglad Bez Zapisu", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 200
        assert await _blog_post_of(db_session, page.id) is None

    @patch("monolynx.services.wiki.get_markdown", return_value="# Szkic\n\nTresc")
    async def test_preview_does_not_commit(self, mock_get_md, client, db_session, monkeypatch):
        project = await _create_project(db_session, "m264-prev-nocommit")
        user = await _login_regular_member(client, db_session, project, "m264-prev-nocommit@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Podglad Bez Commita", is_public=False)
        commits: list[int] = []
        original_commit = db_session.commit

        async def spy_commit():
            commits.append(1)
            await original_commit()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 200
        assert commits == []

    @patch("monolynx.services.wiki.get_markdown", return_value="# Szkic\n\nTresc")
    async def test_preview_shows_metadata_of_existing_post_row(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-meta")
        user = await _login_regular_member(client, db_session, project, "m264-prev-meta@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Podglad Z Metadanymi", is_public=False)
        await _add_blog_post(db_session, page, slug="szkic-meta-m264", description="Opis szkicu MON264", author_name="Autor Szkicu MON264")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 200
        assert "Opis szkicu MON264" in resp.text
        assert "Autor Szkicu MON264" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Publiczny\n\nTresc publiczna")
    async def test_preview_of_public_page_also_carries_noindex(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-pub")
        user = await _login_regular_member(client, db_session, project, "m264-prev-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Podglad Publicznej", is_public=True)
        await _add_blog_post(db_session, page, slug="podglad-publicznej-m264")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 200
        assert resp.headers.get("x-robots-tag") == "noindex"
        assert "Podgląd szkicu" in resp.text

    async def test_preview_without_session_redirects_to_login(self, client, db_session):
        project = await _create_project(db_session, "m264-prev-anon")
        user = User(email="m264-prev-anon-setup@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        page = await _create_wiki_page(db_session, project, user, title="Podglad Bez Sesji")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview", follow_redirects=False)

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @patch("monolynx.services.wiki.get_markdown", return_value="# Szkic\n\nTresc sekretna MON264")
    async def test_preview_without_wiki_read_is_forbidden(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-403")
        user = await _login_regular_member(client, db_session, project, "m264-prev-403@test.com", permissions={"scrum": ["read"]})
        page = await _create_wiki_page(db_session, project, user, title="Podglad Bez Uprawnienia", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 403
        assert "Tresc sekretna MON264" not in resp.text
        assert "Podgląd szkicu" not in resp.text

    async def test_preview_of_unknown_page_returns_404(self, client, db_session):
        project = await _create_project(db_session, "m264-prev-nopage")
        await _login_regular_member(client, db_session, project, "m264-prev-nopage@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{uuid.uuid4()}/blog-preview")

        assert resp.status_code == 404

    async def test_preview_of_unknown_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="m264-prev-noproj@test.com")

        resp = await client.get(f"/dashboard/nie-ma-takiego-projektu-m264/wiki/pages/{uuid.uuid4()}/blog-preview")

        assert resp.status_code == 404

    @patch("monolynx.services.wiki.get_markdown", return_value="# Cudza\n\nTresc cudza MON264")
    async def test_preview_of_page_from_another_project_returns_404(self, mock_get_md, client, db_session):
        mine = await _create_project(db_session, "m264-prev-mine")
        theirs = await _create_project(db_session, "m264-prev-theirs")
        user = await _login_regular_member(client, db_session, mine, "m264-prev-cross@test.com")
        foreign_page = await _create_wiki_page(db_session, theirs, user, title="Cudza Strona Podgladu", is_public=False)

        resp = await client.get(f"/dashboard/{mine.slug}/wiki/pages/{foreign_page.id}/blog-preview")

        assert resp.status_code == 404
        assert "Tresc cudza MON264" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", side_effect=Exception("MinIO niedostepne"))
    async def test_preview_storage_error_returns_404(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-storage")
        user = await _login_regular_member(client, db_session, project, "m264-prev-storage@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Podglad Blad Storage", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview")

        assert resp.status_code == 404

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_page_detail_links_to_the_preview(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-prev-link")
        user = await _login_and_add_member(client, db_session, project, "m264-prev-link@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Szczegoly Z Podgladem")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}")

        assert resp.status_code == 200
        assert f"/dashboard/{project.slug}/wiki/pages/{page.id}/blog-preview" in resp.text
        assert "Podgląd wpisu" in resp.text

    @pytest.mark.parametrize(
        "url",
        ["/sitemap.xml", "/blog/feed.xml", "/blog/rss.xml", "/blog/feed.json", "/llms.txt", "/llms-full.txt", "/blog/"],
    )
    @patch("monolynx.services.wiki.get_markdown", return_value="# Wpis\n\nTresc publiczna MON264")
    async def test_preview_address_is_absent_from_sitemap_feeds_and_llms(self, mock_get_md, url, client, db_session):
        """Kryterium 3: obok wpisu publicznego (jest w kazdym z tych adresow) nie ma adresu podgladu."""
        project = await _create_project(db_session, "m264-prev-seo")
        user = await _login_and_add_member(client, db_session, project, "m264-prev-seo@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Poza Indeksem", is_public=True)
        await _add_blog_post(db_session, page, slug="wpis-poza-indeksem-m264")

        resp = await client.get(url)

        assert resp.status_code == 200
        present = "Tresc publiczna MON264" if url == "/llms-full.txt" else "wpis-poza-indeksem-m264"
        assert present in resp.text
        assert "blog-preview" not in resp.text
        assert f"/wiki/pages/{page.id}" not in resp.text


@pytest.mark.integration
class TestWikiUploadForPage:
    """Upload z edytora z `page_id`: zalacznik strony i adres wg stanu publikacji."""

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_to_draft_creates_attachment_and_returns_dashboard_address(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-draft")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-draft@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Do Szkicu", is_public=False)
        slug, page_id = project.slug, page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={page_id}",
            files={"file": ("moje zdjęcie.png", _PNG, "image/png")},
        )

        assert resp.status_code == 200
        attachment = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalar_one()
        assert attachment.mime_type == "image/png"
        assert attachment.size == len(_PNG)
        assert attachment.storage_path.startswith(f"{slug}/wiki-attachments/{page_id}/")
        expected = f"/dashboard/{slug}/wiki/pages/{page_id}/attachments/{attachment.id}/{quote(attachment.filename)}"
        assert resp.json() == {"data": {"filePath": expected}}
        mock_upload.assert_called_once()

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_to_published_page_returns_public_address_under_the_post_slug(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-pub")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Do Publicznej", is_public=True)
        await _add_blog_post(db_session, page, slug="adres-wpisu-inny-niz-strona-m264")
        slug, page_id = project.slug, page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={page_id}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 200
        attachment = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalar_one()
        assert resp.json() == {"data": {"filePath": f"/blog/adres-wpisu-inny-niz-strona-m264/attachments/{attachment.id}/obraz.png"}}

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_to_unpublished_page_with_post_row_still_returns_dashboard_address(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-withdrawn")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-withdrawn@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Do Cofnietej", is_public=False)
        await _add_blog_post(db_session, page, slug="cofniety-wpis-m264")
        slug, page_id = project.slug, page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={page_id}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 200
        attachment = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalar_one()
        assert resp.json() == {
            "data": {"filePath": f"/dashboard/{slug}/wiki/pages/{page_id}/attachments/{attachment.id}/obraz.png"},
        }

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_commits_the_attachment(self, mock_upload, client, db_session, monkeypatch):
        project = await _create_project(db_session, "m264-upl-commit")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-commit@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Z Commitem", is_public=False)
        commits: list[int] = []
        original_commit = db_session.commit

        async def spy_commit():
            commits.append(1)
            await original_commit()

        monkeypatch.setattr(db_session, "commit", spy_commit)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload?page_id={page.id}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 200
        assert commits != []

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_with_page_of_another_project_returns_404_and_stores_nothing(self, mock_upload, client, db_session):
        mine = await _create_project(db_session, "m264-upl-mine")
        theirs = await _create_project(db_session, "m264-upl-theirs")
        user = await _login_and_add_member(client, db_session, mine, "m264-upl-cross@test.com")
        foreign_page = await _create_wiki_page(db_session, theirs, user, title="Cudza Strona Uploadu")
        slug, foreign_page_id = mine.slug, foreign_page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={foreign_page_id}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 404
        assert "error" in resp.json()
        mock_upload.assert_not_called()
        stored = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == foreign_page_id))).scalars().all()
        assert stored == []

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_upload_with_unknown_page_returns_404(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-nopage")
        await _login_and_add_member(client, db_session, project, "m264-upl-nopage@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/upload?page_id={uuid.uuid4()}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 404
        mock_upload.assert_not_called()

    @patch("monolynx.dashboard.wiki.minio_upload_object", side_effect=Exception("MinIO error"))
    async def test_upload_storage_error_returns_500_and_stores_no_attachment(self, mock_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-500")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-500@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Blad Storage", is_public=False)
        slug, page_id = project.slug, page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={page_id}",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 500
        assert "error" in resp.json()
        stored = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalars().all()
        assert stored == []

    @patch("monolynx.dashboard.wiki.minio_upload_object")
    @patch("monolynx.dashboard.wiki.upload_attachment", return_value="m264-upl-legacy/attachments/abc123.png")
    async def test_upload_without_page_id_keeps_legacy_behaviour(self, mock_legacy, mock_page_upload, client, db_session):
        project = await _create_project(db_session, "m264-upl-legacy")
        user = await _login_and_add_member(client, db_session, project, "m264-upl-legacy@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Bez Page Id")
        slug, page_id = project.slug, page.id

        resp = await client.post(
            f"/dashboard/{slug}/wiki/upload",
            files={"file": ("obraz.png", _PNG, "image/png")},
        )

        assert resp.status_code == 200
        assert resp.json() == {"data": {"filePath": f"/dashboard/{slug}/wiki/attachments/abc123.png"}}
        mock_legacy.assert_called_once()
        mock_page_upload.assert_not_called()
        stored = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalars().all()
        assert stored == []


@pytest.mark.integration
class TestWikiEditorImageReachesAnonymousReader:
    """Kryteria 5 i 6: obrazek wgrany w dashboardzie jest widoczny anonimowo po publikacji; cudzy zalacznik nie jest przepisywany."""

    @patch("monolynx.blog.minio_get_attachment", return_value=(_PNG, "image/png"))
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m264-e2e/fake.md")
    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_image_uploaded_to_draft_is_served_to_anonymous_reader_after_publish(
        self, mock_upload, mock_wiki_upload, mock_embeddings, mock_get_att, client, db_session
    ):
        project = await _create_project(db_session, "m264-e2e")
        user = await _login_and_add_member(client, db_session, project, "m264-e2e@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Wpis Z Obrazkiem E2E", is_public=False)
        slug, page_id = project.slug, page.id

        upload = await client.post(
            f"/dashboard/{slug}/wiki/upload?page_id={page_id}",
            files={"file": ("zdjecie.png", _PNG, "image/png")},
        )
        assert upload.status_code == 200
        editor_address = upload.json()["data"]["filePath"]
        attachment = (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalar_one()
        attachment_id = attachment.id

        content = f"# Wpis z obrazkiem\n\n![Zdjecie testowe]({editor_address})\n"
        publish = await client.post(
            f"/dashboard/{slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, content=content, is_public="on"),
            follow_redirects=False,
        )
        assert publish.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        post_slug = post.slug

        public_address = f"/blog/{post_slug}/attachments/{attachment_id}/zdjecie.png"
        with patch("monolynx.services.wiki.get_markdown", return_value=content):
            async with _anonymous_client() as anonymous:
                page_resp = await anonymous.get(f"/blog/{post_slug}")
                image_resp = await anonymous.get(public_address)

        assert page_resp.status_code == 200
        assert public_address in page_resp.text
        assert f"/dashboard/{slug}/wiki/pages/{page_id}/attachments/" not in page_resp.text
        assert image_resp.status_code == 200
        assert image_resp.content == _PNG

    @patch("monolynx.services.wiki.get_markdown")
    async def test_attachment_of_another_page_in_content_is_not_rewritten_on_the_public_page(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m264-cross")
        user = await _login_and_add_member(client, db_session, project, "m264-cross@test.com")
        published = await _create_wiki_page(db_session, project, user, title="Wpis Z Cudzym Obrazkiem", is_public=True)
        post = await _add_blog_post(db_session, published, slug="wpis-cudzy-obrazek-m264")
        other = await _create_wiki_page(db_session, project, user, title="Strona Z Prywatnym Obrazkiem", is_public=False)
        foreign = await _add_attachment(db_session, other, filename="prywatny.png")
        own = await _add_attachment(db_session, published, filename="wlasny.png")
        foreign_address = f"/dashboard/{project.slug}/wiki/pages/{other.id}/attachments/{foreign.id}/prywatny.png"
        own_address = f"/dashboard/{project.slug}/wiki/pages/{published.id}/attachments/{own.id}/wlasny.png"
        mock_get_md.return_value = f"# Wpis\n\n![Cudzy]({foreign_address})\n\n![Wlasny]({own_address})\n"

        async with _anonymous_client() as anonymous:
            resp = await anonymous.get(f"/blog/{post.slug}")

        assert resp.status_code == 200
        assert foreign_address in resp.text
        assert f"/blog/{post.slug}/attachments/{foreign.id}" not in resp.text
        assert f"/blog/{post.slug}/attachments/{own.id}/wlasny.png" in resp.text
        assert own_address not in resp.text


# ---------------------------------------------------------------------------
# MON-270: is_public w formularzu wiki wymaga blog:write (uprawnienie konta)
#
# `login_session` zawsze tworzy superusera (omija bramkę), więc konta bez uprawnienia i z flagą
# zakładają testy ręcznie: zwykły użytkownik (nie superuser) z rolą `member` (wiki:write).
# `db_session.rollback` jest podmieniony w testach odmowy, bo rollback serwisu cofnąłby całą
# transakcję testu razem z seedem.
# ---------------------------------------------------------------------------


async def _login_plain_member(client, db_session, project, email, *, can_edit_blog):
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=False, can_edit_blog=can_edit_blog)
    db_session.add(user)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="member"))
    await db_session.flush()
    resp = await client.post("/auth/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)
    assert resp.status_code == 303
    return user


async def _pages_titled(db_session, project, title):
    return (await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == title))).scalars().all()


@pytest.mark.integration
class TestWikiFormIsPublicRequiresBlogPermission:
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-create-deny/fake.md")
    async def test_create_public_page_without_flag_is_refused_and_creates_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-create-deny")
        await _login_plain_member(client, db_session, project, "m270-create-deny@test.com", can_edit_blog=False)

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/create",
                data={"title": "Publiczna Bez Prawa M270", "content": "# Tresc", "position": "0", "is_public": "on"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        assert await _pages_titled(db_session, project, "Publiczna Bez Prawa M270") == []
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-create-ok/fake.md")
    async def test_create_public_page_with_flag_creates_page_and_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-create-ok")
        await _login_plain_member(client, db_session, project, "m270-create-ok@test.com", can_edit_blog=True)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Publiczna Z Prawem M270", "content": "# Tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        (page,) = await _pages_titled(db_session, project, "Publiczna Z Prawem M270")
        assert page.is_public is True
        assert await _blog_post_of(db_session, page.id) is not None

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-create-priv/fake.md")
    async def test_create_private_page_without_flag_still_works(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-create-priv")
        await _login_plain_member(client, db_session, project, "m270-create-priv@test.com", can_edit_blog=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Prywatna Bez Prawa M270", "content": "# Tresc", "position": "0"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        (page,) = await _pages_titled(db_session, project, "Prywatna Bez Prawa M270")
        assert page.is_public is False
        assert await _blog_post_of(db_session, page.id) is None

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-child-deny/fake.md")
    async def test_child_create_public_page_without_flag_is_refused_and_creates_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-child-deny")
        user = await _login_plain_member(client, db_session, project, "m270-child-deny@test.com", can_edit_blog=False)
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic M270", is_public=False)

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
                data={"title": "Podstrona Publiczna M270", "content": "# Tresc", "position": "0", "is_public": "on"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        assert await _pages_titled(db_session, project, "Podstrona Publiczna M270") == []
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-child-ok/fake.md")
    async def test_child_create_public_page_with_flag_creates_public_page(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-child-ok")
        user = await _login_plain_member(client, db_session, project, "m270-child-ok@test.com", can_edit_blog=True)
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic Ok M270", is_public=False)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create",
            data={"title": "Podstrona Ok M270", "content": "# Tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        (page,) = await _pages_titled(db_session, project, "Podstrona Ok M270")
        assert page.is_public is True

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-edit-deny/fake.md")
    async def test_edit_publishing_without_flag_is_refused_and_changes_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-edit-deny")
        user = await _login_plain_member(client, db_session, project, "m270-edit-deny@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Do Publikacji M270", is_public=False)
        page_id = page.id

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
                data={"title": "Zmieniony Tytul M270", "content": "Nowa tresc", "position": "5", "is_public": "on"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (False, "Do Publikacji M270", 0)
        assert not re.search(r"\bchecked\b", _is_public_tag(resp.text))
        assert await _blog_post_of(db_session, page_id) is None
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-edit-ok/fake.md")
    async def test_edit_publishing_with_flag_publishes(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-edit-ok")
        user = await _login_plain_member(client, db_session, project, "m270-edit-ok@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Do Publikacji Ok M270", is_public=False)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Do Publikacji Ok M270", "content": "Nowa tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert (await db_session.execute(select(WikiPage.is_public).where(WikiPage.id == page_id))).scalar_one() is True
        assert await _blog_post_of(db_session, page_id) is not None

    @patch("monolynx.dashboard.wiki.set_post_meta")
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Stara tresc")
    async def test_title_and_content_edit_of_public_page_without_flag_is_refused_and_changes_nothing(
        self, mock_get, mock_upload, mock_embeddings, mock_set_meta, client, db_session
    ):
        project = await _create_project(db_session, "m270-keep")
        user = await _login_plain_member(client, db_session, project, "m270-keep@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Edycja M270", is_public=True)
        await _add_blog_post(db_session, page, slug="publiczna-edycja-m270")
        page_id = page.id

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
                data={"title": "Nowy Tytul Publicznej M270", "content": "Nowa tresc", "position": "7"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (True, "Publiczna Edycja M270", 0)
        mock_upload.assert_not_called()
        mock_set_meta.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep-content/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Stara tresc")
    async def test_content_only_edit_of_public_page_without_flag_is_refused_and_stores_nothing(
        self, mock_get, mock_upload, mock_embeddings, client, db_session
    ):
        project = await _create_project(db_session, "m270-keep-content")
        user = await _login_plain_member(client, db_session, project, "m270-keep-content@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Tresc M270", is_public=True)
        page_id = page.id

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
                data={"title": "Publiczna Tresc M270", "content": "Podmieniona tresc", "position": "5"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        saved = (await db_session.execute(select(WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == ("Publiczna Tresc M270", 0)
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep-title/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Ta sama tresc")
    async def test_title_only_edit_of_public_page_without_flag_is_refused_and_changes_nothing(
        self, mock_get, mock_upload, mock_embeddings, client, db_session
    ):
        project = await _create_project(db_session, "m270-keep-title")
        user = await _login_plain_member(client, db_session, project, "m270-keep-title@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczny Tytul M270", is_public=True)
        page_id = page.id

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
                data={"title": "Zmieniony Publiczny Tytul M270", "content": "Ta sama tresc", "position": "5"},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        saved = (await db_session.execute(select(WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == ("Publiczny Tytul M270", 0)
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep-crlf/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Linia pierwsza\nLinia druga")
    async def test_edit_of_public_page_without_flag_with_unchanged_title_and_crlf_content_passes(
        self, mock_get, mock_upload, mock_embeddings, client, db_session
    ):
        """Textarea formularza zwraca CRLF, wiec sama inna konwencja konca linii nie jest zmiana tresci."""
        project = await _create_project(db_session, "m270-keep-crlf")
        user = await _login_plain_member(client, db_session, project, "m270-keep-crlf@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Crlf M270", is_public=True)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Publiczna Crlf M270", "content": "Linia pierwsza\r\nLinia druga", "position": "4"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (True, 4)

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep-flag/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Stara tresc")
    async def test_title_and_content_edit_of_public_page_with_flag_saves(self, mock_get, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-keep-flag")
        user = await _login_plain_member(client, db_session, project, "m270-keep-flag@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Z Flaga M270", is_public=True)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Nowy Tytul Z Flaga M270", "content": "Nowa tresc", "position": "7", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (True, "Nowy Tytul Z Flaga M270", 7)
        mock_upload.assert_called_once()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-priv-edit/fake.md")
    async def test_title_and_content_edit_of_private_page_without_flag_saves(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-priv-edit")
        user = await _login_plain_member(client, db_session, project, "m270-priv-edit@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Prywatna Edycja M270", is_public=False)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Prywatna Po Edycji M270", "content": "Nowa tresc", "position": "2"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (False, "Prywatna Po Edycji M270", 2)
        mock_upload.assert_called_once()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-keep-on/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Nowa tresc")
    async def test_edit_of_public_page_without_flag_with_checked_box_keeps_it_public(
        self, mock_get, mock_upload, mock_embeddings, client, db_session
    ):
        project = await _create_project(db_session, "m270-keep-on")
        user = await _login_plain_member(client, db_session, project, "m270-keep-on@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Zaznaczona M270", is_public=True)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Publiczna Zaznaczona M270", "content": "Nowa tresc", "position": "3", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.position).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (True, 3)

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-unpub/fake.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="Nowa tresc")
    async def test_edit_without_checkbox_by_account_with_flag_still_unpublishes(self, mock_get, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-unpub")
        user = await _login_plain_member(client, db_session, project, "m270-unpub@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Do Cofniecia M270", is_public=True)
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data={"title": "Do Cofniecia M270", "content": "Nowa tresc", "position": "0"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert (await db_session.execute(select(WikiPage.is_public).where(WikiPage.id == page_id))).scalar_one() is False


def _is_public_tag(html: str) -> str:
    tag = _tag(html, "is_public")
    assert tag is not None
    return tag


_ADMIN_NOTE = "Publikację na blogu nadaje administrator."


@pytest.mark.integration
class TestWikiFormBlogControlsDependOnBlogPermission:
    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_of_post_without_flag_hides_blog_section_and_disables_checkbox(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m270-ui-edit-deny")
        user = await _login_plain_member(client, db_session, project, "m270-ui-edit-deny@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Wpis Ui Bez Prawa M270", is_public=True)
        await _add_blog_post(db_session, page, slug="wpis-ui-bez-prawa-m270")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert resp.status_code == 200
        assert 'id="blog-meta-section"' not in resp.text
        assert re.search(r"\bdisabled\b", _is_public_tag(resp.text))
        assert re.search(r"\bchecked\b", _is_public_tag(resp.text))
        assert _ADMIN_NOTE in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Tresc")
    async def test_edit_form_of_post_with_flag_shows_blog_section_and_enabled_checkbox(self, mock_get_md, client, db_session):
        project = await _create_project(db_session, "m270-ui-edit-ok")
        user = await _login_plain_member(client, db_session, project, "m270-ui-edit-ok@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Wpis Ui Z Prawem M270", is_public=True)
        await _add_blog_post(db_session, page, slug="wpis-ui-z-prawem-m270")

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit")

        assert resp.status_code == 200
        assert 'id="blog-meta-section"' in resp.text
        assert not re.search(r"\bdisabled\b", _is_public_tag(resp.text))
        assert _ADMIN_NOTE not in resp.text

    @patch("monolynx.services.embeddings.update_page_embeddings")
    async def test_create_form_without_flag_disables_checkbox_and_explains(self, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-ui-create-deny")
        await _login_plain_member(client, db_session, project, "m270-ui-create-deny@test.com", can_edit_blog=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")

        assert resp.status_code == 200
        assert re.search(r"\bdisabled\b", _is_public_tag(resp.text))
        assert _ADMIN_NOTE in resp.text

    @patch("monolynx.services.embeddings.update_page_embeddings")
    async def test_create_form_with_flag_has_enabled_checkbox(self, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-ui-create-ok")
        await _login_plain_member(client, db_session, project, "m270-ui-create-ok@test.com", can_edit_blog=True)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/create")

        assert resp.status_code == 200
        assert not re.search(r"\bdisabled\b", _is_public_tag(resp.text))
        assert _ADMIN_NOTE not in resp.text

    @patch("monolynx.services.embeddings.update_page_embeddings")
    async def test_child_create_form_without_flag_disables_checkbox(self, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-ui-child-deny")
        user = await _login_plain_member(client, db_session, project, "m270-ui-child-deny@test.com", can_edit_blog=False)
        parent = await _create_wiki_page(db_session, project, user, title="Rodzic Ui M270", is_public=False)

        resp = await client.get(f"/dashboard/{project.slug}/wiki/pages/{parent.id}/create")

        assert resp.status_code == 200
        assert re.search(r"\bdisabled\b", _is_public_tag(resp.text))

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-form-err/fake.md")
    async def test_validation_error_without_flag_keeps_checkbox_of_a_public_page_checked(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-form-err-deny")
        user = await _login_plain_member(client, db_session, project, "m270-form-err-deny@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Blad M270", is_public=True)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "", "content": "Nowa tresc", "position": "0"},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert re.search(r"\bchecked\b", _is_public_tag(resp.text))
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-form-err/fake.md")
    async def test_validation_error_with_flag_renders_checkbox_as_submitted(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-form-err-ok")
        user = await _login_plain_member(client, db_session, project, "m270-form-err-ok@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Publiczna Blad Ok M270", is_public=True)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "", "content": "Nowa tresc", "position": "0"},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert not re.search(r"\bchecked\b", _is_public_tag(resp.text))
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-meta-deny/fake.md")
    async def test_edit_with_blog_meta_marker_without_flag_is_refused_and_changes_nothing(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-meta-deny")
        user = await _login_plain_member(client, db_session, project, "m270-meta-deny@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title="Wpis Meta Bez Prawa M270", is_public=False)
        await _add_blog_post(db_session, page, slug=page.slug, description="Stary opis")
        page_id = page.id

        with patch.object(db_session, "rollback", AsyncMock()):
            resp = await client.post(
                f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
                data=_default_blog_form(page, title="Zmieniony Tytul Meta M270", content="Nowa tresc", blog_description="Nowy opis bez prawa"),
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Brak uprawnienia blog:write" in resp.text
        saved = (await db_session.execute(select(WikiPage.is_public, WikiPage.title).where(WikiPage.id == page_id))).one()
        assert tuple(saved) == (False, "Wpis Meta Bez Prawa M270")
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.description == "Stary opis"
        mock_upload.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="m270-meta-ok/fake.md")
    async def test_edit_with_blog_meta_marker_with_flag_saves_metadata(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "m270-meta-ok")
        user = await _login_plain_member(client, db_session, project, "m270-meta-ok@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title="Wpis Meta Z Prawem M270", is_public=False)
        await _add_blog_post(db_session, page, slug=page.slug, description="Stary opis")
        page_id = page.id

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page_id}/edit",
            data=_default_blog_form(page, title="Zmieniony Tytul Meta Ok M270", content="Nowa tresc", blog_description="Nowy opis z prawem"),
            follow_redirects=False,
        )

        assert resp.status_code == 303
        post = await _blog_post_of(db_session, page_id)
        assert post is not None
        assert post.description == "Nowy opis z prawem"


# ---------------------------------------------------------------------------
# MON-272 pkt 2 i 11: upload do strony publicznej wymaga blog:write; komunikat błędu z polskimi znakami
# ---------------------------------------------------------------------------


async def _post_page_upload(client, slug, page_id, channel):
    """Wysyła plik do strony kanałem panelu: `editor` (EasyMDE, `/wiki/upload?page_id=`) albo `filepond` (załączniki strony)."""
    if channel == "editor":
        return await client.post(f"/dashboard/{slug}/wiki/upload?page_id={page_id}", files={"file": ("obraz.png", _PNG, "image/png")})
    return await client.post(f"/dashboard/{slug}/wiki/pages/{page_id}/attachments/upload", files={"filepond": ("obraz.png", _PNG, "image/png")})


async def _attachments_of(db_session, page_id):
    return (await db_session.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == page_id))).scalars().all()


_UPLOAD_CHANNELS = pytest.mark.parametrize("channel", ["editor", "filepond"])


@pytest.mark.integration
class TestMon272PublicPageUploadNeedsBlogPermission:
    @_UPLOAD_CHANNELS
    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_mon272_upload_to_public_page_without_blog_permission_returns_403_and_stores_nothing(
        self, mock_upload, channel, client, db_session
    ):
        """MON-272 pkt 2: konto bez blog:write nie dodaje pliku do strony publicznej (brak zapisu w MinIO i w bazie)."""
        project = await _create_project(db_session, f"m272-upl-deny-{channel}")
        user = await _login_plain_member(client, db_session, project, f"m272-upl-deny-{channel}@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title=f"Publiczna Upload Odmowa {channel}", is_public=True)
        slug, page_id = project.slug, page.id

        resp = await _post_page_upload(client, slug, page_id, channel)

        assert resp.status_code == 403
        assert await _attachments_of(db_session, page_id) == []
        mock_upload.assert_not_called()

    @_UPLOAD_CHANNELS
    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_mon272_upload_to_public_page_with_blog_permission_succeeds(self, mock_upload, channel, client, db_session):
        """MON-272 pkt 2: konto z blog:write dodaje plik do strony publicznej."""
        project = await _create_project(db_session, f"m272-upl-ok-{channel}")
        user = await _login_plain_member(client, db_session, project, f"m272-upl-ok-{channel}@test.com", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, user, title=f"Publiczna Upload Zgoda {channel}", is_public=True)
        slug, page_id = project.slug, page.id

        resp = await _post_page_upload(client, slug, page_id, channel)

        assert resp.status_code == 200
        assert len(await _attachments_of(db_session, page_id)) == 1
        mock_upload.assert_called_once()

    @_UPLOAD_CHANNELS
    @patch("monolynx.dashboard.wiki.minio_upload_object")
    async def test_mon272_upload_to_private_page_needs_only_wiki_write(self, mock_upload, channel, client, db_session):
        """MON-272 pkt 2: strona prywatna bez zmian - wystarczy wiki:write, blog:write nie jest wymagane."""
        project = await _create_project(db_session, f"m272-upl-priv-{channel}")
        user = await _login_plain_member(client, db_session, project, f"m272-upl-priv-{channel}@test.com", can_edit_blog=False)
        page = await _create_wiki_page(db_session, project, user, title=f"Prywatna Upload {channel}", is_public=False)
        slug, page_id = project.slug, page.id

        resp = await _post_page_upload(client, slug, page_id, channel)

        assert resp.status_code == 200
        assert len(await _attachments_of(db_session, page_id)) == 1
        mock_upload.assert_called_once()

    @patch("monolynx.dashboard.wiki.minio_upload_object", side_effect=Exception("MinIO error"))
    async def test_mon272_editor_upload_storage_error_message_has_polish_diacritics(self, mock_upload, client, db_session):
        """MON-272 pkt 11: błąd uploadu z edytora zwraca komunikat z polskimi znakami."""
        project = await _create_project(db_session, "m272-upl-diacritics")
        user = await _login_and_add_member(client, db_session, project, "m272-upl-diacritics@test.com")
        page = await _create_wiki_page(db_session, project, user, title="Upload Diakrytyki M272", is_public=False)
        slug, page_id = project.slug, page.id

        resp = await client.post(f"/dashboard/{slug}/wiki/upload?page_id={page_id}", files={"file": ("obraz.png", _PNG, "image/png")})

        assert resp.status_code == 500
        assert resp.json()["error"] == "Błąd uploadu pliku"
