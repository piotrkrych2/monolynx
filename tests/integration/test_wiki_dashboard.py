"""Testy integracyjne -- dashboard wiki (drzewo stron, CRUD, upload, wyszukiwanie)."""

import secrets
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password
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


async def _create_wiki_page(db_session, project, user, title="Test Page", content="# Test", parent_id=None):
    """Helper: tworzy strone wiki bezposrednio w DB (bez MinIO)."""
    page = WikiPage(
        project_id=project.id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
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
