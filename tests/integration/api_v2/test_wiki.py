"""Testy integracyjne API v2 -- Wiki (MON-142).

Pokrywa 8 endpointów `src/monolynx/api/v2/wiki.py`: list/get/create/update/
delete strony, search, get/set config. Wołane serwisy: `services/wiki.py`
(MON-139), `services/embeddings.py` (`search_wiki_pages`).

MinIO nie działa w testach -- `upload_markdown`/`get_markdown`/`delete_object`
są zawsze mockowane na poziomie `monolynx.services.wiki.*` (wzorzec z
`tests/integration/test_wiki_service_extraction.py`). `OPENAI_API_KEY` jest
puste w env testowym, więc `services/embeddings.py::is_enabled()` zwraca
False i `search_wiki_pages` daje pustą listę -- test search asertuje na tym
udokumentowanym stanie, nie próbuje mockować realnego embeddingu.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from monolynx.models.wiki_page import WikiPage

from .conftest import add_member, make_actor, make_owner_project

pytestmark = pytest.mark.integration


async def _make_page(db_session, project, user, *, slug: str, title: str, parent_id=None, position: int = 0) -> WikiPage:
    page = WikiPage(
        project_id=project.id,
        parent_id=parent_id,
        title=title,
        slug=slug,
        position=position,
        minio_path=f"wiki/{project.slug}/{slug}.md",
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    db_session.add(page)
    await db_session.flush()
    return page


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/wiki/pages
# ---------------------------------------------------------------------------


class TestListWikiPages:
    async def test_happy_path_fields_and_depth(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-wiki")
        root = await _make_page(db_session, project, actor.user, slug="root-page", title="Strona glowna")
        child = await _make_page(db_session, project, actor.user, slug="child-page", title="Podstrona", parent_id=root.id)

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/pages", headers=actor.headers)

        assert response.status_code == 200
        items = {i["id"]: i for i in response.json()["items"]}
        assert items[str(root.id)]["depth"] == 0
        assert items[str(root.id)]["parent_id"] is None
        assert items[str(child.id)]["depth"] == 1
        assert items[str(child.id)]["parent_id"] == str(root.id)
        assert items[str(child.id)]["title"] == "Podstrona"

    async def test_non_member_returns_403(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "list-wiki-403")
        outsider = await make_actor(db_session, "list-wiki-403-out")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/pages", headers=outsider.headers)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/wiki/pages/{page_id}
# ---------------------------------------------------------------------------


class TestGetWikiPage:
    @patch("monolynx.services.wiki.get_markdown")
    async def test_happy_path(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "# Tresc strony wiki"
        actor, project = await make_owner_project(db_session, "get-wiki-page")
        page = await _make_page(db_session, project, actor.user, slug="strona-x", title="Strona X")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/pages/{page.id}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Strona X"
        assert body["content"] == "# Tresc strony wiki"
        assert body["parent_id"] is None

    async def test_nonexistent_page_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-wiki-page-404")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/pages/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{slug}/wiki/pages
# ---------------------------------------------------------------------------


class TestCreateWikiPage:
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_happy_path(self, mock_upload_markdown, client, db_session):
        mock_upload_markdown.return_value = "wiki/mock/path.md"
        actor, project = await make_owner_project(db_session, "create-wiki-page")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/wiki/pages",
            headers=actor.headers,
            json={"title": "Nowa strona API v2", "content": "Tresc nowej strony"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Nowa strona API v2"
        mock_upload_markdown.assert_called_once()

        db_page = (await db_session.execute(select(WikiPage).where(WikiPage.id == uuid.UUID(body["id"])))).scalar_one()
        assert db_page.slug == "nowa-strona-api-v2"

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_reserved_slug_returns_422(self, mock_upload_markdown, client, db_session):
        actor, project = await make_owner_project(db_session, "create-wiki-reserved")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/wiki/pages",
            headers=actor.headers,
            json={"title": "Wiki Index", "content": "x"},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        mock_upload_markdown.assert_not_called()

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_mon272_public_page_with_slug_over_255_chars_returns_422_not_500(self, mock_upload_markdown, mock_embeddings, client, db_session):
        """MON-272 pkt 9: tytuł dający slug ponad limit blog_posts.slug (255) to 422 problem+json z czytelnym komunikatem."""
        mock_upload_markdown.return_value = "wiki/mock/path.md"
        actor, project = await make_owner_project(db_session, "create-wiki-longslug", can_edit_blog=True)
        title = " ".join(["slowo"] * 60)

        with patch.object(db_session, "rollback", AsyncMock()):
            response = await client.post(
                f"/api/v2/projects/{project.slug}/wiki/pages",
                headers=actor.headers,
                json={"title": title, "content": "x", "is_public": True},
            )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        detail = response.json()["detail"]
        assert "za długi" in detail
        assert "255" in detail


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/wiki/pages/{page_id}
# ---------------------------------------------------------------------------


class TestUpdateWikiPage:
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_happy_path_updates_content(self, mock_upload_markdown, client, db_session):
        actor, project = await make_owner_project(db_session, "update-wiki-page")
        page = await _make_page(db_session, project, actor.user, slug="do-edycji", title="Przed edycja")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/wiki/pages/{page.id}",
            headers=actor.headers,
            json={"title": "Po edycji", "content": "Nowa tresc"},
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Po edycji"
        mock_upload_markdown.assert_called_once()
        await db_session.refresh(page)
        assert page.title == "Po edycji"

    async def test_nonexistent_page_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "update-wiki-page-404")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/wiki/pages/{uuid.uuid4()}",
            headers=actor.headers,
            json={"title": "x"},
        )

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v2/projects/{slug}/wiki/pages/{page_id}
# ---------------------------------------------------------------------------


class TestDeleteWikiPage:
    @patch("monolynx.services.wiki.delete_object")
    async def test_happy_path(self, mock_delete_object, client, db_session):
        actor, project = await make_owner_project(db_session, "delete-wiki-page")
        page = await _make_page(db_session, project, actor.user, slug="do-usuniecia", title="Do usuniecia")

        response = await client.delete(f"/api/v2/projects/{project.slug}/wiki/pages/{page.id}", headers=actor.headers)

        assert response.status_code == 204
        assert response.content == b""
        mock_delete_object.assert_called_once()

        remaining = (await db_session.execute(select(WikiPage).where(WikiPage.id == page.id))).scalar_one_or_none()
        assert remaining is None

    @patch("monolynx.services.wiki.delete_object")
    async def test_deletes_children_too(self, mock_delete_object, client, db_session):
        actor, project = await make_owner_project(db_session, "delete-wiki-page-children")
        root = await _make_page(db_session, project, actor.user, slug="rodzic", title="Rodzic")
        child = await _make_page(db_session, project, actor.user, slug="dziecko", title="Dziecko", parent_id=root.id)

        response = await client.delete(f"/api/v2/projects/{project.slug}/wiki/pages/{root.id}", headers=actor.headers)

        assert response.status_code == 204
        remaining_child = (await db_session.execute(select(WikiPage).where(WikiPage.id == child.id))).scalar_one_or_none()
        assert remaining_child is None


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/wiki/search
# ---------------------------------------------------------------------------


class TestSearchWiki:
    async def test_returns_plain_array_not_paginated(self, client, db_session):
        """OPENAI_API_KEY jest puste w env testowym -> embeddings.is_enabled()
        zwraca False -> search_wiki_pages daje []. Kontrakt: goly JSON array,
        nie PaginatedResponse (bez "items"/"page")."""
        actor, project = await make_owner_project(db_session, "search-wiki")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/search?query=cokolwiek", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert body == []

    async def test_missing_query_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "search-wiki-missing-query")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/search", headers=actor.headers)

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/wiki/config
# ---------------------------------------------------------------------------


class TestGetWikiConfig:
    async def test_happy_path_defaults_disabled(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-wiki-config")

        response = await client.get(f"/api/v2/projects/{project.slug}/wiki/config", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["wiki_llm_enabled"] is False


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/wiki/config
# ---------------------------------------------------------------------------


class TestSetWikiConfig:
    async def test_happy_path_enables(self, client, db_session):
        actor, project = await make_owner_project(db_session, "set-wiki-config")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/wiki/config",
            headers=actor.headers,
            json={"enabled": True},
        )

        assert response.status_code == 200
        assert response.json()["wiki_llm_enabled"] is True
        await db_session.refresh(project)
        assert project.wiki_llm_enabled is True

    async def test_member_without_settings_write_gets_403(self, client, db_session):
        """Serwis set_wiki_config() ma wlasny check_permission (settings:write)
        i tez zwrociłby 403 z INNYM tekstem ("Brak uprawnienia do zmiany
        konfiguracji projektu (settings:write)") -- detail z require_permission()
        w api/v2/common.py ("Brak uprawnienia settings:write") celuje wprost w
        warstwe routera, nie w serwis (patrz reguła w MEMORY.md: dwie warstwy,
        ta sama odmowa, asercja na efekt konkretnej warstwy)."""
        _actor, project = await make_owner_project(db_session, "set-wiki-config-403")
        member_actor = await make_actor(db_session, "set-wiki-config-403-member")
        await add_member(db_session, project, member_actor.user, role="member")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/wiki/config",
            headers=member_actor.headers,
            json={"enabled": True},
        )

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Brak uprawnienia settings:write"
        await db_session.refresh(project)
        assert project.wiki_llm_enabled is False
