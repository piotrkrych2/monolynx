"""Testy integracyjne API v2 -- Blog (MON-261, MON-262).

Pokrywa 4 endpointy `src/monolynx/api/v2/blog.py`: lista wpisów (z szkicami), szczegóły wpisu
z treścią, częściowy PATCH metadanych i lint wpisu (MON-262). Logika w `services/blog.py` (`list_posts`,
`get_post`, `set_post_meta`) i `services/blog_lint.py` (`lint_post_by_id`); tu sprawdzamy kontrakt HTTP: kształt odpowiedzi, paginację, rozróżnienie
"pole nieobecne / null / []" w PATCH, mapowanie błędów na problem+json i uprawnienia `wiki:*`.

MinIO nie działa w testach -- treść wpisu jest mockowana na `monolynx.services.wiki.get_markdown`
(wzorzec `test_wiki.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.blog_post import BlogPost
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_page import WikiPage

from .conftest import make_actor, make_owner_project, make_project

pytestmark = pytest.mark.integration

_ITEM_KEYS = {
    "page_id",
    "title",
    "slug",
    "previous_slugs",
    "description",
    "published_at",
    "author_name",
    "tags",
    "lang",
    "translation_of_id",
    "cover_attachment_id",
    "last_verified_at",
    "is_public",
    "public_url",
    "markdown_url",
}


async def _page(db_session, project, user, *, is_public: bool = False, with_post: bool = True, **post_fields) -> WikiPage:
    slug = f"blog-v2-{uuid.uuid4().hex[:8]}"
    page = WikiPage(
        project_id=project.id,
        title=f"Tytuł {slug}",
        slug=slug,
        position=0,
        minio_path=f"wiki/{project.slug}/{slug}.md",
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    db_session.add(page)
    await db_session.flush()
    if with_post:
        db_session.add(BlogPost(page_id=page.id, slug=slug, **post_fields))
        await db_session.flush()
    return page


async def _row(db_session, page_id) -> BlogPost:
    db_session.expire_all()
    return (await db_session.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one()


async def _actor_with_permissions(db_session, project, tag: str, permissions: dict[str, list[str]]):
    actor = await make_actor(db_session, tag)
    role = Role(name=f"role-{tag}-{uuid.uuid4().hex[:6]}", project_id=project.id, permissions=permissions, is_system=False)
    db_session.add(role)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=actor.user.id, role="member", role_id=role.id))
    await db_session.flush()
    return actor


def _posts_url(project) -> str:
    return f"/api/v2/projects/{project.slug}/blog/posts"


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/blog/posts
# ---------------------------------------------------------------------------


class TestListBlogPosts:
    async def test_lists_drafts_and_published_with_full_shape(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-list")
        published_at = datetime(2026, 4, 5, 6, 7, tzinfo=UTC)
        published = await _page(
            db_session, project, actor.user, is_public=True, published_at=published_at, tags=["a"], last_verified_at=date(2026, 4, 1)
        )
        draft = await _page(db_session, project, actor.user, is_public=False)
        await _page(db_session, project, actor.user, with_post=False)

        response = await client.get(_posts_url(project), headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert (body["page"], body["per_page"], body["total"], body["total_pages"]) == (1, 100, 2, 1)
        items = {i["page_id"]: i for i in body["items"]}
        assert set(items) == {str(published.id), str(draft.id)}
        pub = items[str(published.id)]
        assert set(pub) == _ITEM_KEYS
        assert datetime.fromisoformat(pub["published_at"]) == published_at
        assert pub["last_verified_at"] == "2026-04-01"
        assert pub["is_public"] is True
        assert pub["public_url"].endswith(f"/blog/{published.slug}")
        assert pub["markdown_url"] == pub["public_url"] + ".md"
        dr = items[str(draft.id)]
        assert dr["is_public"] is False
        assert dr["public_url"] is None
        assert dr["markdown_url"] is None
        assert dr["published_at"] is None

    async def test_status_filter(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-list-status")
        published = await _page(db_session, project, actor.user, is_public=True)
        draft = await _page(db_session, project, actor.user, is_public=False)

        pub_resp = await client.get(_posts_url(project), params={"status": "published"}, headers=actor.headers)
        draft_resp = await client.get(_posts_url(project), params={"status": "draft"}, headers=actor.headers)

        assert [i["page_id"] for i in pub_resp.json()["items"]] == [str(published.id)]
        assert [i["page_id"] for i in draft_resp.json()["items"]] == [str(draft.id)]

    async def test_invalid_status_returns_422_problem_json(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-list-badstatus")

        response = await client.get(_posts_url(project), params={"status": "archived"}, headers=actor.headers)

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    async def test_pagination_and_ordering(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-list-page")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        # Kolejność wstawiania celowo różna od oczekiwanej (malejący published_at).
        middle = await _page(db_session, project, actor.user, published_at=base + timedelta(days=5))
        oldest = await _page(db_session, project, actor.user, published_at=base)
        newest = await _page(db_session, project, actor.user, published_at=base + timedelta(days=10))

        first = await client.get(_posts_url(project), params={"per_page": 2, "page": 1}, headers=actor.headers)
        second = await client.get(_posts_url(project), params={"per_page": 2, "page": 2}, headers=actor.headers)

        assert [i["page_id"] for i in first.json()["items"]] == [str(newest.id), str(middle.id)]
        assert [i["page_id"] for i in second.json()["items"]] == [str(oldest.id)]
        assert (first.json()["total"], first.json()["total_pages"], first.json()["per_page"]) == (3, 2, 2)
        assert second.json()["page"] == 2

    async def test_other_project_posts_are_not_listed(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-list-scope")
        other = await make_project(db_session, "blog-list-scope-other")
        mine = await _page(db_session, project, actor.user)
        await _page(db_session, other, actor.user)

        response = await client.get(_posts_url(project), headers=actor.headers)

        assert [i["page_id"] for i in response.json()["items"]] == [str(mine.id)]

    async def test_missing_token_returns_401_problem_json(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "blog-list-401")

        response = await client.get(_posts_url(project))

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"

    async def test_non_member_returns_403(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "blog-list-403")
        outsider = await make_actor(db_session, "blog-list-403-out")

        response = await client.get(_posts_url(project), headers=outsider.headers)

        assert response.status_code == 403

    async def test_unknown_project_returns_404(self, client, db_session):
        actor = await make_actor(db_session, "blog-list-404")

        response = await client.get("/api/v2/projects/nie-ma-takiego-projektu/blog/posts", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_role_without_wiki_read_returns_403_with_permission_detail(self, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-list-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-list-perm-a", {"scrum": ["read"]})

        response = await client.get(_posts_url(project), headers=actor.headers)

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert "Brak uprawnienia wiki:read" in response.json()["detail"]

    async def test_read_only_wiki_role_can_list(self, client, db_session):
        owner, project = await make_owner_project(db_session, "blog-list-ro")
        actor = await _actor_with_permissions(db_session, project, "blog-list-ro-a", {"wiki": ["read"]})
        page = await _page(db_session, project, owner.user)

        response = await client.get(_posts_url(project), headers=actor.headers)

        assert response.status_code == 200
        assert [i["page_id"] for i in response.json()["items"]] == [str(page.id)]


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/blog/posts/{page_id}
# ---------------------------------------------------------------------------


class TestGetBlogPost:
    @patch("monolynx.services.wiki.get_markdown")
    async def test_published_post_has_urls_and_content(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "# Treść wpisu"
        actor, project = await make_owner_project(db_session, "blog-get-pub")
        page = await _page(db_session, project, actor.user, is_public=True, description="Opis", lang="en", tags=["x", "y"])

        response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == _ITEM_KEYS | {"content"}
        assert body["content"] == "# Treść wpisu"
        assert body["page_id"] == str(page.id)
        assert body["title"] == page.title
        assert body["description"] == "Opis"
        assert body["lang"] == "en"
        assert body["tags"] == ["x", "y"]
        assert body["is_public"] is True
        assert body["public_url"].endswith(f"/blog/{page.slug}")
        assert body["markdown_url"] == body["public_url"] + ".md"

    @patch("monolynx.services.wiki.get_markdown")
    async def test_draft_post_has_no_urls(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "szkic"
        actor, project = await make_owner_project(db_session, "blog-get-draft")
        page = await _page(db_session, project, actor.user, is_public=False)

        response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["is_public"] is False
        assert body["public_url"] is None
        assert body["markdown_url"] is None

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_storage_failure_returns_503_problem_json(self, _mock_content, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-storage")
        page = await _page(db_session, project, actor.user, is_public=True)

        response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 503
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["title"] == "Service Unavailable"
        assert body["detail"] == "Treść wpisu jest chwilowo niedostępna"

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_missing_post_row_stays_404_when_storage_is_down(self, _mock_content, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-storage-norow")
        page = await _page(db_session, project, actor.user, with_post=False)

        response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 404
        assert response.json()["detail"] == "Wpis bloga nie istnieje"

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_role_without_wiki_read_stays_403_when_storage_is_down(self, _mock_content, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-get-storage-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-get-storage-perm-a", {"scrum": ["read"]})

        response = await client.get(f"{_posts_url(project)}/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 403
        assert "Brak uprawnienia wiki:read" in response.json()["detail"]

    async def test_nonexistent_page_returns_404_page_detail(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-404")

        response = await client.get(f"{_posts_url(project)}/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Strona wiki nie istnieje"

    async def test_page_without_post_row_returns_404_post_detail(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-norow")
        page = await _page(db_session, project, actor.user, with_post=False)

        response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Wpis bloga nie istnieje"

    async def test_page_from_other_project_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-foreign")
        other = await make_project(db_session, "blog-get-foreign-other")
        foreign = await _page(db_session, other, actor.user)

        response = await client.get(f"{_posts_url(project)}/{foreign.id}", headers=actor.headers)

        assert response.status_code == 404
        assert response.json()["detail"] == "Strona wiki nie istnieje"

    async def test_malformed_page_id_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-422")

        response = await client.get(f"{_posts_url(project)}/nie-uuid", headers=actor.headers)

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    async def test_role_without_wiki_read_gets_403_even_for_nonexistent_page(self, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-get-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-get-perm-a", {"scrum": ["read"]})

        response = await client.get(f"{_posts_url(project)}/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 403
        assert "Brak uprawnienia wiki:read" in response.json()["detail"]

    async def test_missing_token_returns_401(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-get-401")
        page = await _page(db_session, project, actor.user)

        response = await client.get(f"{_posts_url(project)}/{page.id}")

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/blog/posts/{page_id}/lint (MON-262)
# ---------------------------------------------------------------------------

_LINT_KEYS = {"page_id", "score", "error_count", "warning_count", "info_count", "issues"}
_LINT_DESCRIPTION = "Opis wpisu bloga o lincie: sprawdza strukturę, grafiki i metadane tak, żeby agent AI mógł go czytać."


def _lint_url(project, page_id) -> str:
    return f"{_posts_url(project)}/{page_id}/lint"


class TestLintBlogPost:
    @patch("monolynx.services.wiki.get_markdown")
    async def test_returns_shape_score_counts_and_sorted_issues(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "# Tytuł\n\nWstęp bez streszczenia.\n"
        actor, project = await make_owner_project(db_session, "blog-lint-ok")
        page = await _page(db_session, project, actor.user, description=_LINT_DESCRIPTION, last_verified_at=date.today())

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == _LINT_KEYS
        assert body["page_id"] == str(page.id)
        assert body["score"] == 75
        assert (body["error_count"], body["warning_count"], body["info_count"]) == (1, 1, 1)
        assert [(i["line"], i["level"], i["rule"]) for i in body["issues"]] == [
            (0, "warning", "h2-no-question"),
            (0, "info", "translation-missing"),
            (3, "error", "tldr-missing"),
        ]
        assert all(set(i) == {"rule", "level", "line", "message"} for i in body["issues"])
        assert all(isinstance(i["message"], str) and i["message"].strip() for i in body["issues"])

    @patch("monolynx.services.wiki.get_markdown")
    async def test_clean_post_has_no_issues_and_full_score(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = (
            "> [!TLDR]\n> - Pierwszy punkt streszczenia.\n> - Drugi punkt streszczenia.\n\n## Czym jest lint?\n\nLint sprawdza wpis.\n"
        )
        actor, project = await make_owner_project(db_session, "blog-lint-clean")
        original = await _page(db_session, project, actor.user)
        page = await _page(
            db_session, project, actor.user, description=_LINT_DESCRIPTION, last_verified_at=date.today(), translation_of_id=original.id
        )

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["issues"] == []
        assert body["score"] == 100
        assert (body["error_count"], body["warning_count"], body["info_count"]) == (0, 0, 0)

    @patch("monolynx.services.wiki.get_markdown")
    async def test_row_metadata_reaches_the_lint(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "> [!TLDR]\n> - Pierwszy.\n> - Drugi.\n\n## Czym jest lint?\n\nTekst.\n"
        actor, project = await make_owner_project(db_session, "blog-lint-meta")
        page = await _page(db_session, project, actor.user, description="", last_verified_at=date.today() - timedelta(days=400))

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        rules = {i["rule"] for i in response.json()["issues"]}
        assert {"description-missing", "last-verified-stale"} <= rules
        assert "last-verified-missing" not in rules

    @patch("monolynx.services.wiki.get_markdown")
    async def test_lint_does_not_modify_the_post_row(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Wstęp bez streszczenia.\n"
        actor, project = await make_owner_project(db_session, "blog-lint-readonly")
        page = await _page(db_session, project, actor.user, description="Bez zmian", tags=["x"])

        await client.get(_lint_url(project, page.id), headers=actor.headers)

        row = await _row(db_session, page.id)
        assert (row.description, row.tags, row.last_verified_at) == ("Bez zmian", ["x"], None)

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_storage_failure_returns_503_problem_json(self, _mock_content, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-storage")
        page = await _page(db_session, project, actor.user)

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 503
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["title"] == "Service Unavailable"

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_missing_post_row_stays_404_when_storage_is_down(self, _mock_content, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-storage-norow")
        page = await _page(db_session, project, actor.user, with_post=False)

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 404
        assert response.json()["detail"] == "Wpis bloga nie istnieje"

    @patch("monolynx.services.wiki.get_page_content", side_effect=RuntimeError("minio down"))
    async def test_role_without_wiki_read_stays_403_when_storage_is_down(self, _mock_content, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-lint-storage-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-lint-storage-perm-a", {"scrum": ["read"]})

        response = await client.get(_lint_url(project, uuid.uuid4()), headers=actor.headers)

        assert response.status_code == 403
        assert "Brak uprawnienia wiki:read" in response.json()["detail"]

    async def test_nonexistent_page_returns_404_page_detail(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-404")

        response = await client.get(_lint_url(project, uuid.uuid4()), headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Strona wiki nie istnieje"

    async def test_page_without_post_row_returns_404_post_detail(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-norow")
        page = await _page(db_session, project, actor.user, with_post=False)

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Wpis bloga nie istnieje"

    async def test_page_from_other_project_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-foreign")
        other = await make_project(db_session, "blog-lint-foreign-other")
        foreign = await _page(db_session, other, actor.user)

        response = await client.get(_lint_url(project, foreign.id), headers=actor.headers)

        assert response.status_code == 404
        assert response.json()["detail"] == "Strona wiki nie istnieje"

    async def test_malformed_page_id_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-422")

        response = await client.get(f"{_posts_url(project)}/nie-uuid/lint", headers=actor.headers)

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    async def test_role_without_wiki_read_gets_403_even_for_nonexistent_page(self, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-lint-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-lint-perm-a", {"scrum": ["read"]})

        response = await client.get(_lint_url(project, uuid.uuid4()), headers=actor.headers)

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert "Brak uprawnienia wiki:read" in response.json()["detail"]

    @patch("monolynx.services.wiki.get_markdown")
    async def test_read_only_wiki_role_can_lint(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Wstęp.\n"
        owner, project = await make_owner_project(db_session, "blog-lint-readonly-role")
        page = await _page(db_session, project, owner.user)
        actor = await _actor_with_permissions(db_session, project, "blog-lint-readonly-role-a", {"wiki": ["read"]})

        response = await client.get(_lint_url(project, page.id), headers=actor.headers)

        assert response.status_code == 200

    async def test_non_member_returns_403(self, client, db_session):
        owner, project = await make_owner_project(db_session, "blog-lint-nonmember")
        page = await _page(db_session, project, owner.user)
        outsider = await make_actor(db_session, "blog-lint-nonmember-out")

        response = await client.get(_lint_url(project, page.id), headers=outsider.headers)

        assert response.status_code == 403

    async def test_missing_token_returns_401(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-lint-401")
        page = await _page(db_session, project, actor.user)

        response = await client.get(_lint_url(project, page.id))

        assert response.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/blog/posts/{page_id}
# ---------------------------------------------------------------------------


class TestPatchBlogPost:
    async def test_absent_fields_stay_unchanged(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-absent")
        original = await _page(db_session, project, actor.user)
        page = await _page(
            db_session,
            project,
            actor.user,
            description="Stary opis",
            tags=["a", "b"],
            author_name="Autor",
            translation_of_id=original.id,
            last_verified_at=date(2026, 1, 2),
        )
        page_id, original_id = page.id, original.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"lang": "en"})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == _ITEM_KEYS
        assert body["lang"] == "en"
        row = await _row(db_session, page_id)
        assert (row.lang, row.description, row.tags, row.author_name) == ("en", "Stary opis", ["a", "b"], "Autor")
        assert row.translation_of_id == original_id
        assert row.last_verified_at == date(2026, 1, 2)

    async def test_empty_body_returns_200_and_changes_nothing(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-empty")
        page = await _page(db_session, project, actor.user, description="Bez zmian", tags=["a"])
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={})

        assert response.status_code == 200
        assert response.json()["description"] == "Bez zmian"
        row = await _row(db_session, page_id)
        assert (row.description, row.tags) == ("Bez zmian", ["a"])

    async def test_null_clears_nullable_fields(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-null")
        original = await _page(db_session, project, actor.user)
        page = await _page(db_session, project, actor.user, translation_of_id=original.id, last_verified_at=date(2026, 1, 2))
        att = WikiAttachment(wiki_page_id=page.id, filename="c.png", storage_path=f"wiki-attachments/{page.id}/c.png", mime_type="image/png", size=1)
        db_session.add(att)
        await db_session.flush()
        page_id, att_id = page.id, att.id
        url = f"{_posts_url(project)}/{page_id}"
        (await _row(db_session, page_id)).cover_attachment_id = att_id
        await db_session.flush()

        response = await client.patch(
            url,
            headers=actor.headers,
            json={"translation_of_id": None, "cover_attachment_id": None, "last_verified_at": None},
        )

        assert response.status_code == 200
        body = response.json()
        assert (body["translation_of_id"], body["cover_attachment_id"], body["last_verified_at"]) == (None, None, None)
        row = await _row(db_session, page_id)
        assert (row.translation_of_id, row.cover_attachment_id, row.last_verified_at) == (None, None, None)

    async def test_sets_uuid_and_date_values(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-set")
        original = await _page(db_session, project, actor.user)
        page = await _page(db_session, project, actor.user)
        page_id, original_id = page.id, original.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(
            url,
            headers=actor.headers,
            json={"translation_of_id": str(original_id), "last_verified_at": "2026-05-06", "author_name": "Ada"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["translation_of_id"] == str(original_id)
        assert body["last_verified_at"] == "2026-05-06"
        assert body["author_name"] == "Ada"
        row = await _row(db_session, page_id)
        assert row.translation_of_id == original_id
        assert row.last_verified_at == date(2026, 5, 6)

    async def test_empty_tags_list_clears_tags(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-tags")
        page = await _page(db_session, project, actor.user, tags=["a", "b"])
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"tags": []})

        assert response.status_code == 200
        assert response.json()["tags"] == []
        assert (await _row(db_session, page_id)).tags == []

    async def test_tags_list_replaces_tags(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-tags-set")
        page = await _page(db_session, project, actor.user, tags=["a", "b"])
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"tags": ["c", "d"]})

        assert response.status_code == 200
        assert response.json()["tags"] == ["c", "d"]
        assert (await _row(db_session, page_id)).tags == ["c", "d"]

    async def test_slug_change_moves_old_slug_to_previous_slugs(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-slug")
        page = await _page(db_session, project, actor.user)
        old_slug = page.slug
        url = f"{_posts_url(project)}/{page.id}"
        new_slug = f"nowy-{uuid.uuid4().hex[:8]}"

        response = await client.patch(url, headers=actor.headers, json={"slug": new_slug})

        assert response.status_code == 200
        body = response.json()
        assert body["slug"] == new_slug
        assert body["previous_slugs"] == [old_slug]

    async def test_slug_taken_by_other_post_returns_422_and_changes_nothing(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-slug-taken")
        other = await _page(db_session, project, actor.user)
        page = await _page(db_session, project, actor.user, description="Bez zmian")
        page_id, page_slug, other_slug = page.id, page.slug, other.slug
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"slug": other_slug, "description": "Nie zapisze się"})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        row = await _row(db_session, page_id)
        assert (row.slug, row.description) == (page_slug, "Bez zmian")

    @pytest.mark.parametrize("field", ["description", "author_name", "lang", "tags", "slug"])
    async def test_null_for_non_nullable_field_returns_422_and_changes_nothing(self, client, db_session, field):
        actor, project = await make_owner_project(db_session, f"blog-patch-null-{field}")
        page = await _page(db_session, project, actor.user, description="Bez zmian", lang="pl")
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"lang": "en", field: None})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        row = await _row(db_session, page_id)
        assert (row.lang, row.description) == ("pl", "Bez zmian")

    async def test_invalid_lang_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-lang")
        page = await _page(db_session, project, actor.user)
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"lang": "de"})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert (await _row(db_session, page_id)).lang == "pl"

    @pytest.mark.parametrize(
        "body",
        [
            {"translation_of_id": "nie-uuid"},
            {"cover_attachment_id": "nie-uuid"},
            {"last_verified_at": "31/12/2026"},
        ],
    )
    async def test_malformed_uuid_or_date_in_body_returns_422(self, client, db_session, body):
        actor, project = await make_owner_project(db_session, "blog-patch-badbody")
        page = await _page(db_session, project, actor.user, description="Bez zmian")
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"description": "Nie zapisze się", **body})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert (await _row(db_session, page_id)).description == "Bez zmian"

    async def test_malformed_page_id_in_path_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-badpath")

        response = await client.patch(f"{_posts_url(project)}/nie-uuid", headers=actor.headers, json={"description": "x"})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"

    async def test_published_at_and_is_public_cannot_be_changed_through_patch(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-nopub")
        page = await _page(db_session, project, actor.user, is_public=False)
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        await client.patch(
            url,
            headers=actor.headers,
            json={"published_at": "2030-01-01T00:00:00Z", "is_public": True, "description": "Opis"},
        )

        assert (await _row(db_session, page_id)).published_at is None
        db_session.expire_all()
        refreshed = (await db_session.execute(select(WikiPage).where(WikiPage.id == page_id))).scalar_one()
        assert refreshed.is_public is False

    async def test_nonexistent_page_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-404")

        response = await client.patch(f"{_posts_url(project)}/{uuid.uuid4()}", headers=actor.headers, json={"description": "x"})

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["detail"] == "Strona wiki nie istnieje"

    async def test_page_from_other_project_returns_404_and_creates_no_row(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-foreign")
        other = await make_project(db_session, "blog-patch-foreign-other")
        foreign = await _page(db_session, other, actor.user, with_post=False)
        foreign_id = foreign.id
        url = f"{_posts_url(project)}/{foreign_id}"

        response = await client.patch(url, headers=actor.headers, json={"description": "x"})

        assert response.status_code == 404
        db_session.expire_all()
        count = (await db_session.execute(select(BlogPost).where(BlogPost.page_id == foreign_id))).scalar_one_or_none()
        assert count is None

    async def test_page_without_post_row_gets_row_created(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-create")
        page = await _page(db_session, project, actor.user, with_post=False)
        page_id = page.id
        url = f"{_posts_url(project)}/{page_id}"

        response = await client.patch(url, headers=actor.headers, json={"description": "Nowy wpis"})

        assert response.status_code == 200
        assert response.json()["description"] == "Nowy wpis"
        assert (await _row(db_session, page_id)).description == "Nowy wpis"

    async def test_draft_response_has_no_urls(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-draft")
        page = await _page(db_session, project, actor.user, is_public=False)

        response = await client.patch(f"{_posts_url(project)}/{page.id}", headers=actor.headers, json={"description": "Opis"})

        body = response.json()
        assert (body["is_public"], body["public_url"], body["markdown_url"]) == (False, None, None)

    async def test_published_response_has_urls(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-pub")
        page = await _page(db_session, project, actor.user, is_public=True)
        page_slug = page.slug
        url = f"{_posts_url(project)}/{page.id}"

        response = await client.patch(url, headers=actor.headers, json={"description": "Opis"})

        body = response.json()
        assert body["is_public"] is True
        assert body["public_url"].endswith(f"/blog/{page_slug}")
        assert body["markdown_url"] == body["public_url"] + ".md"

    async def test_missing_token_returns_401(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blog-patch-401")
        page = await _page(db_session, project, actor.user)

        response = await client.patch(f"{_posts_url(project)}/{page.id}", json={"description": "x"})

        assert response.status_code == 401

    async def test_non_member_returns_403(self, client, db_session):
        owner, project = await make_owner_project(db_session, "blog-patch-403")
        page = await _page(db_session, project, owner.user)
        outsider = await make_actor(db_session, "blog-patch-403-out")

        response = await client.patch(f"{_posts_url(project)}/{page.id}", headers=outsider.headers, json={"description": "x"})

        assert response.status_code == 403

    async def test_read_only_wiki_role_gets_403_and_nothing_is_written(self, client, db_session):
        owner, project = await make_owner_project(db_session, "blog-patch-ro")
        actor = await _actor_with_permissions(db_session, project, "blog-patch-ro-a", {"wiki": ["read"]})
        page = await _page(db_session, project, owner.user, description="Bez zmian")

        response = await client.patch(f"{_posts_url(project)}/{page.id}", headers=actor.headers, json={"description": "Nie zapisze się"})

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert "Brak uprawnienia wiki:write" in response.json()["detail"]
        assert (await _row(db_session, page.id)).description == "Bez zmian"

    async def test_role_without_wiki_write_gets_403_even_for_nonexistent_page(self, client, db_session):
        _owner, project = await make_owner_project(db_session, "blog-patch-perm")
        actor = await _actor_with_permissions(db_session, project, "blog-patch-perm-a", {"wiki": ["read"]})

        response = await client.patch(f"{_posts_url(project)}/{uuid.uuid4()}", headers=actor.headers, json={"description": "x"})

        assert response.status_code == 403
        assert "Brak uprawnienia wiki:write" in response.json()["detail"]

    async def test_read_only_wiki_role_can_still_get_the_post(self, client, db_session):
        owner, project = await make_owner_project(db_session, "blog-patch-ro-get")
        actor = await _actor_with_permissions(db_session, project, "blog-patch-ro-get-a", {"wiki": ["read"]})
        page = await _page(db_session, project, owner.user)

        with patch("monolynx.services.wiki.get_markdown", return_value="x"):
            response = await client.get(f"{_posts_url(project)}/{page.id}", headers=actor.headers)

        assert response.status_code == 200
