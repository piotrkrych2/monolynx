"""Uprawnienie `blog:write` konta w kanałach MCP i API v2 (MON-270, K1, K2, K6, K8).

Kontrakt: zapisy bloga (`set_blog_post_meta`, `publish_blog_post`, `unpublish_blog_post`) oraz zmiana widoczności
strony wiki (`create_wiki_page(is_public=True)`, `update_wiki_page` ze zmienioną `is_public`) wymagają konta, które jest
superuserem albo ma `can_edit_blog=True`. Rola w projekcie (owner, admin, member) tego nie zastępuje:

- odmowa to `ValueError("Brak uprawnienia blog:write")` w MCP i 403 `application/problem+json` z tym samym `detail`
  w API v2,
- po odmowie stan bazy (strona, wpis bloga, liczba stron i wpisów) jest identyczny jak przed wywołaniem, więc zapis
  przed bramką albo po niej też wychodzi,
- to samo wywołanie na koncie z uprawnieniem (flaga albo superuser) przechodzi i zmienia stan (para pozytywna),
- edycja tytułu albo treści strony, która jest już publiczna, wymaga tego samego uprawnienia (MON-271); niezmieniony
  tytuł i treść (także różniące się tylko CRLF/LF albo spacjami wokół tytułu) oraz strony prywatne go nie wymagają,
- uprawnienie jest czytane z konta w chwili wywołania: odebranie flagi blokuje kolejny zapis tym samym tokenem,
  nadanie go odblokowuje.

Aktorzy: osobny użytkownik z tokenem na kombinację (rola w projekcie, superuser, flaga). MinIO i embeddingi są
zamockowane (MinIO jako słownik w pamięci); sesja testowa jest współdzielona przez oba kanały, więc testy nie
dowodzą commita (`db-commit.md`; commit pokrywają `test_api_v2_persistence.py` i `test_mcp_tools.py`).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx import mcp_server
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.wiki_page import WikiPage
from tests.integration.api_v2.conftest import ApiV2Actor, add_member, make_actor, make_project

pytestmark = pytest.mark.integration

_DENIED = "Brak uprawnienia blog:write"
_PROBLEM_JSON = "application/problem+json"
_DESCRIPTION = "Opis wpisu bloga o uprawnieniu: sprawdza bramkę konta i pilnuje, żeby agent AI mógł go czytać."
_CONTENT = "> [!TLDR]\n> - Pierwszy punkt streszczenia.\n> - Drugi punkt streszczenia.\n\n## Czym jest bramka?\n\nBramka sprawdza konto.\n"


@dataclass(frozen=True)
class Op:
    """Jedna operacja zapisu: wywołanie MCP, odpowiednik v2 i warunek, że zapis zmienił stan strony."""

    name: str
    starts_public: bool
    mcp_tool: str
    mcp_kwargs: dict[str, Any]
    v2_method: str
    v2_suffix: str
    v2_json: dict[str, Any] | None
    changed: Any


OPS: list[Op] = [
    Op(
        "set_meta",
        False,
        "set_blog_post_meta",
        {"description": "Nowy opis po zmianie"},
        "PATCH",
        "blog/posts/{page_id}",
        {"description": "Nowy opis po zmianie"},
        lambda s: s["description"] == "Nowy opis po zmianie",
    ),
    Op(
        "publish",
        False,
        "publish_blog_post",
        {"force": True},
        "POST",
        "blog/posts/{page_id}/publish",
        {"force": True},
        lambda s: s["is_public"] is True,
    ),
    Op("unpublish", True, "unpublish_blog_post", {}, "POST", "blog/posts/{page_id}/unpublish", None, lambda s: s["is_public"] is False),
    Op(
        "wiki_publish",
        False,
        "update_wiki_page",
        {"is_public": True},
        "PATCH",
        "wiki/pages/{page_id}",
        {"is_public": True},
        lambda s: s["is_public"] is True,
    ),
    Op(
        "wiki_unpublish",
        True,
        "update_wiki_page",
        {"is_public": False},
        "PATCH",
        "wiki/pages/{page_id}",
        {"is_public": False},
        lambda s: s["is_public"] is False,
    ),
]
_OP_IDS = [op.name for op in OPS]

# (rola w projekcie, superuser, flaga can_edit_blog)
_DENIED_ACTORS = [("owner", False, False), ("admin", False, False), ("member", False, False)]
_ALLOWED_ACTORS = [("owner", False, True), ("admin", False, True), ("member", False, True), ("member", True, False), ("owner", True, False)]
_DENIED_IDS = [f"{role}-noflag" for role, _su, _flag in _DENIED_ACTORS]
_ALLOWED_IDS = [f"{role}-{'super' if su else 'flag'}" for role, su, _flag in _ALLOWED_ACTORS]


@dataclass
class Env:
    db: AsyncSession
    client: httpx.AsyncClient
    project: Project
    minio: dict[str, str]

    async def actor(self, role: str, *, superuser: bool, flag: bool) -> ApiV2Actor:
        actor = await make_actor(self.db, f"bperm-{role}", is_superuser=superuser, can_edit_blog=flag)
        await add_member(self.db, self.project, actor.user, role=role)
        return actor

    async def page(self, *, public: bool, title: str | None = None) -> uuid.UUID:
        page_id = uuid.uuid4()
        path = f"{self.project.slug}/{page_id}.md"
        self.minio[path] = _CONTENT
        owner = await make_actor(self.db, "bperm-author")
        page = WikiPage(
            id=page_id,
            project_id=self.project.id,
            title=title or f"Wpis {page_id.hex[:6]}",
            slug=f"wpis-{page_id.hex[:10]}",
            position=0,
            minio_path=path,
            is_public=public,
            created_by_id=owner.user.id,
            last_edited_by_id=owner.user.id,
        )
        self.db.add(page)
        await self.db.flush()
        self.db.add(
            BlogPost(
                page_id=page_id,
                slug=page.slug,
                description=_DESCRIPTION,
                tags=["start"],
                last_verified_at=date.today(),
                published_at=None,
            )
        )
        await self.db.flush()
        return page_id

    async def snapshot(self, page_id: uuid.UUID) -> dict[str, Any]:
        await self.db.flush()
        page = (await self.db.execute(select(WikiPage.is_public, WikiPage.title, WikiPage.position).where(WikiPage.id == page_id))).one()
        post = (
            await self.db.execute(
                select(BlogPost.description, BlogPost.tags, BlogPost.lang, BlogPost.published_at, BlogPost.slug).where(BlogPost.page_id == page_id)
            )
        ).one()
        pages = (await self.db.execute(select(func.count()).select_from(WikiPage).where(WikiPage.project_id == self.project.id))).scalar_one()
        posts_query = select(func.count()).select_from(BlogPost).join(WikiPage, WikiPage.id == BlogPost.page_id)
        posts = (await self.db.execute(posts_query.where(WikiPage.project_id == self.project.id))).scalar_one()
        return {
            "is_public": page.is_public,
            "title": page.title,
            "position": page.position,
            "description": post.description,
            "tags": list(post.tags),
            "lang": post.lang,
            "published_at": post.published_at,
            "slug": post.slug,
            "pages": pages,
            "posts": posts,
        }

    async def mcp(self, actor: ApiV2Actor, tool: str, page_id: uuid.UUID, **kwargs: Any) -> Any:
        return await getattr(mcp_server, tool)(_mcp_ctx(actor), self.project.slug, str(page_id), **kwargs)

    async def mcp_error(self, actor: ApiV2Actor, tool: str, page_id: uuid.UUID, **kwargs: Any) -> str:
        with pytest.raises(ValueError) as info:
            await self.mcp(actor, tool, page_id, **kwargs)
        return str(info.value)

    async def v2(self, actor: ApiV2Actor, op: Op, page_id: uuid.UUID) -> httpx.Response:
        path = f"/api/v2/projects/{self.project.slug}/" + op.v2_suffix.format(page_id=page_id)
        return await self.client.request(op.v2_method, path, headers=actor.headers, json=op.v2_json)


def _mcp_ctx(actor: ApiV2Actor) -> MagicMock:
    token = actor.headers["Authorization"].removeprefix("Bearer ")
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _assert_denied_problem(response: httpx.Response) -> None:
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(_PROBLEM_JSON)
    assert response.json()["detail"] == _DENIED


@pytest.fixture
async def env(db_session, client, monkeypatch) -> Env:
    minio: dict[str, str] = {}

    def upload_markdown(project_slug: str, page_id: uuid.UUID, content: str) -> str:
        path = f"{project_slug}/{page_id}.md"
        minio[path] = content
        return path

    monkeypatch.setattr("monolynx.services.wiki.upload_markdown", upload_markdown)
    monkeypatch.setattr("monolynx.services.wiki.get_markdown", lambda path: minio[path])
    monkeypatch.setattr("monolynx.services.wiki.delete_object", lambda path: minio.pop(path, None))
    monkeypatch.setattr("monolynx.services.embeddings.update_page_embeddings", AsyncMock())

    @asynccontextmanager
    async def session_factory():
        yield db_session

    monkeypatch.setattr(mcp_server, "async_session_factory", session_factory)
    project = await make_project(db_session, "bperm")
    return Env(db=db_session, client=client, project=project, minio=minio)


class TestAccountWithoutPermissionIsDenied:
    @pytest.mark.parametrize("op", OPS, ids=_OP_IDS)
    @pytest.mark.parametrize(("role", "superuser", "flag"), _DENIED_ACTORS, ids=_DENIED_IDS)
    async def test_mcp_denies_and_leaves_state_unchanged(self, env, op, role, superuser, flag):
        actor = await env.actor(role, superuser=superuser, flag=flag)
        page_id = await env.page(public=op.starts_public)
        before = await env.snapshot(page_id)

        message = await env.mcp_error(actor, op.mcp_tool, page_id, **op.mcp_kwargs)

        assert message == _DENIED
        assert await env.snapshot(page_id) == before

    @pytest.mark.parametrize("op", OPS, ids=_OP_IDS)
    @pytest.mark.parametrize(("role", "superuser", "flag"), _DENIED_ACTORS, ids=_DENIED_IDS)
    async def test_v2_denies_with_403_problem_json_and_leaves_state_unchanged(self, env, op, role, superuser, flag):
        actor = await env.actor(role, superuser=superuser, flag=flag)
        page_id = await env.page(public=op.starts_public)
        before = await env.snapshot(page_id)

        response = await env.v2(actor, op, page_id)

        _assert_denied_problem(response)
        assert await env.snapshot(page_id) == before

    @pytest.mark.parametrize("role", ["owner", "admin", "member"])
    async def test_mcp_wiki_create_public_is_denied_and_creates_nothing(self, env, role):
        actor = await env.actor(role, superuser=False, flag=False)
        title = f"Publiczna bez prawa {uuid.uuid4().hex[:6]}"

        with pytest.raises(ValueError) as info:
            await mcp_server.create_wiki_page(_mcp_ctx(actor), env.project.slug, title, "# Treść", is_public=True)

        assert str(info.value) == _DENIED
        assert (await env.db.execute(select(WikiPage.id).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).first() is None
        assert env.minio == {}

    @pytest.mark.parametrize("role", ["owner", "admin", "member"])
    async def test_v2_wiki_create_public_is_denied_and_creates_nothing(self, env, role):
        actor = await env.actor(role, superuser=False, flag=False)
        title = f"Publiczna v2 bez prawa {uuid.uuid4().hex[:6]}"

        response = await env.client.post(
            f"/api/v2/projects/{env.project.slug}/wiki/pages", headers=actor.headers, json={"title": title, "content": "# Treść", "is_public": True}
        )

        _assert_denied_problem(response)
        assert (await env.db.execute(select(WikiPage.id).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).first() is None
        assert env.minio == {}

    async def test_denied_blog_write_does_not_hide_wiki_write_for_private_content(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False)

        await env.mcp(actor, "update_wiki_page", page_id, content="# Nowa treść")
        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"title": "Zmieniony tytuł v2"}
        )

        assert response.status_code == 200
        snapshot = await env.snapshot(page_id)
        assert (snapshot["title"], snapshot["is_public"]) == ("Zmieniony tytuł v2", False)
        assert env.minio[f"{env.project.slug}/{page_id}.md"] == "# Nowa treść"

    async def test_public_page_title_edit_without_visibility_change_is_denied(self, env):
        actor = await env.actor("owner", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł przed zmianą")
        before = await env.snapshot(page_id)
        storage_before = dict(env.minio)

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"title": "Nowy tytuł", "is_public": True}
        )

        _assert_denied_problem(response)
        assert await env.snapshot(page_id) == before
        assert env.minio == storage_before

    async def test_public_page_title_edit_by_account_with_flag_succeeds(self, env):
        actor = await env.actor("owner", superuser=False, flag=True)
        page_id = await env.page(public=True, title="Tytuł przed zmianą flaga")

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}",
            headers=actor.headers,
            json={"title": "Nowy tytuł z flagą", "is_public": True},
        )

        assert response.status_code == 200
        snapshot = await env.snapshot(page_id)
        assert (snapshot["title"], snapshot["is_public"]) == ("Nowy tytuł z flagą", True)

    async def test_v2_public_page_title_only_edit_is_denied_and_changes_nothing(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł tylko tytuł")
        before = await env.snapshot(page_id)

        response = await env.client.patch(f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"title": "Inny"})

        _assert_denied_problem(response)
        assert await env.snapshot(page_id) == before

    async def test_v2_public_page_content_edit_is_denied_and_stores_nothing(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True)
        before = await env.snapshot(page_id)
        storage_before = dict(env.minio)

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"content": "# Podmieniona treść publiczna"}
        )

        _assert_denied_problem(response)
        assert await env.snapshot(page_id) == before
        assert env.minio == storage_before

    async def test_mcp_public_page_content_edit_is_denied_and_stores_nothing(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True)
        before = await env.snapshot(page_id)
        storage_before = dict(env.minio)

        message = await env.mcp_error(actor, "update_wiki_page", page_id, content="# Podmieniona treść publiczna MCP")

        assert message == _DENIED
        assert await env.snapshot(page_id) == before
        assert env.minio == storage_before

    async def test_mcp_public_page_title_edit_is_denied_and_changes_nothing(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł MCP przed")
        before = await env.snapshot(page_id)

        message = await env.mcp_error(actor, "update_wiki_page", page_id, title="Tytuł MCP po")

        assert message == _DENIED
        assert await env.snapshot(page_id) == before

    async def test_mcp_public_page_content_edit_by_account_with_flag_stores_content(self, env):
        actor = await env.actor("member", superuser=False, flag=True)
        page_id = await env.page(public=True)

        await env.mcp(actor, "update_wiki_page", page_id, content="# Nowa treść publiczna z flagą")

        assert env.minio[f"{env.project.slug}/{page_id}.md"] == "# Nowa treść publiczna z flagą"

    async def test_v2_public_page_content_edit_by_superuser_stores_content(self, env):
        actor = await env.actor("member", superuser=True, flag=False)
        page_id = await env.page(public=True)

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"content": "# Treść superusera"}
        )

        assert response.status_code == 200
        assert env.minio[f"{env.project.slug}/{page_id}.md"] == "# Treść superusera"

    async def test_public_page_with_unchanged_title_and_content_passes_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł bez zmiany")

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}",
            headers=actor.headers,
            json={"title": "Tytuł bez zmiany", "content": _CONTENT, "position": 4},
        )

        assert response.status_code == 200
        snapshot = await env.snapshot(page_id)
        assert (snapshot["title"], snapshot["position"], snapshot["is_public"]) == ("Tytuł bez zmiany", 4, True)

    async def test_public_page_with_title_differing_only_by_surrounding_whitespace_passes_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł ze spacjami")

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"title": "  Tytuł ze spacjami  ", "position": 2}
        )

        assert response.status_code == 200
        assert (await env.snapshot(page_id))["position"] == 2

    async def test_public_page_content_differing_only_by_line_endings_passes_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True, title="Tytuł CRLF")

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}",
            headers=actor.headers,
            json={"title": "Tytuł CRLF", "content": _CONTENT.replace("\n", "\r\n"), "position": 6},
        )

        assert response.status_code == 200
        assert (await env.snapshot(page_id))["position"] == 6

    async def test_public_page_position_only_edit_passes_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=True)

        response = await env.client.patch(f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"position": 9})

        assert response.status_code == 200
        assert (await env.snapshot(page_id))["position"] == 9

    async def test_private_page_title_and_content_edit_passes_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False, title="Prywatna przed")

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}",
            headers=actor.headers,
            json={"title": "Prywatna po", "content": "# Prywatna nowa treść"},
        )

        assert response.status_code == 200
        assert (await env.snapshot(page_id))["title"] == "Prywatna po"
        assert env.minio[f"{env.project.slug}/{page_id}.md"] == "# Prywatna nowa treść"


class TestContentEditBumpsUpdatedAt:
    """Edycja treści tym samym użytkownikiem nie zmienia żadnej kolumny strony, więc bez jawnego `updated_at` nie ma UPDATE."""

    async def test_v2_content_edit_by_the_last_editor_bumps_updated_at(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False)
        old = datetime(2020, 1, 1, tzinfo=UTC)
        await env.db.execute(update(WikiPage).where(WikiPage.id == page_id).values(updated_at=old, last_edited_by_id=actor.user.id))
        await env.db.flush()

        response = await env.client.patch(
            f"/api/v2/projects/{env.project.slug}/wiki/pages/{page_id}", headers=actor.headers, json={"content": "# Zmieniona treść"}
        )

        assert response.status_code == 200
        updated_at = (await env.db.execute(select(WikiPage.updated_at).where(WikiPage.id == page_id))).scalar_one()
        assert updated_at > old

    async def test_mcp_content_edit_by_the_last_editor_bumps_updated_at(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False)
        old = datetime(2020, 1, 1, tzinfo=UTC)
        await env.db.execute(update(WikiPage).where(WikiPage.id == page_id).values(updated_at=old, last_edited_by_id=actor.user.id))
        await env.db.flush()

        await env.mcp(actor, "update_wiki_page", page_id, content="# Zmieniona treść MCP")

        updated_at = (await env.db.execute(select(WikiPage.updated_at).where(WikiPage.id == page_id))).scalar_one()
        assert updated_at > old


class TestAccountWithPermissionPasses:
    @pytest.mark.parametrize("op", OPS, ids=_OP_IDS)
    @pytest.mark.parametrize(("role", "superuser", "flag"), _ALLOWED_ACTORS, ids=_ALLOWED_IDS)
    async def test_mcp_applies_the_same_call(self, env, op, role, superuser, flag):
        actor = await env.actor(role, superuser=superuser, flag=flag)
        page_id = await env.page(public=op.starts_public)
        before = await env.snapshot(page_id)

        await env.mcp(actor, op.mcp_tool, page_id, **op.mcp_kwargs)

        after = await env.snapshot(page_id)
        assert after != before
        assert op.changed(after)

    @pytest.mark.parametrize("op", OPS, ids=_OP_IDS)
    @pytest.mark.parametrize(("role", "superuser", "flag"), _ALLOWED_ACTORS, ids=_ALLOWED_IDS)
    async def test_v2_applies_the_same_call(self, env, op, role, superuser, flag):
        actor = await env.actor(role, superuser=superuser, flag=flag)
        page_id = await env.page(public=op.starts_public)
        before = await env.snapshot(page_id)

        response = await env.v2(actor, op, page_id)

        assert response.status_code == 200
        after = await env.snapshot(page_id)
        assert after != before
        assert op.changed(after)

    @pytest.mark.parametrize("superuser", [False, True], ids=["flag", "super"])
    async def test_mcp_wiki_create_public_creates_page_and_blog_post(self, env, superuser):
        actor = await env.actor("member", superuser=superuser, flag=not superuser)
        title = f"Publiczna z prawem {uuid.uuid4().hex[:6]}"

        await mcp_server.create_wiki_page(_mcp_ctx(actor), env.project.slug, title, "# Treść", is_public=True)

        page = (await env.db.execute(select(WikiPage).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).scalar_one()
        assert page.is_public is True
        assert (await env.db.execute(select(BlogPost.page_id).where(BlogPost.page_id == page.id))).scalar_one() == page.id

    @pytest.mark.parametrize("superuser", [False, True], ids=["flag", "super"])
    async def test_v2_wiki_create_public_creates_page_and_blog_post(self, env, superuser):
        actor = await env.actor("member", superuser=superuser, flag=not superuser)
        title = f"Publiczna v2 z prawem {uuid.uuid4().hex[:6]}"

        response = await env.client.post(
            f"/api/v2/projects/{env.project.slug}/wiki/pages", headers=actor.headers, json={"title": title, "content": "# Treść", "is_public": True}
        )

        assert response.status_code == 201
        page = (await env.db.execute(select(WikiPage).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).scalar_one()
        assert page.is_public is True
        assert (await env.db.execute(select(BlogPost.page_id).where(BlogPost.page_id == page.id))).scalar_one() == page.id

    async def test_private_wiki_create_works_without_permission(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        title = f"Prywatna {uuid.uuid4().hex[:6]}"

        response = await env.client.post(
            f"/api/v2/projects/{env.project.slug}/wiki/pages", headers=actor.headers, json={"title": title, "content": "# Treść"}
        )

        assert response.status_code == 201
        page = (await env.db.execute(select(WikiPage).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).scalar_one()
        assert page.is_public is False


class TestPermissionIsReadFromTheAccountAtCallTime:
    async def test_mcp_granting_the_flag_unblocks_the_same_token(self, env):
        actor = await env.actor("owner", superuser=False, flag=False)
        page_id = await env.page(public=False)
        denied = await env.mcp_error(actor, "publish_blog_post", page_id, force=True)

        actor.user.can_edit_blog = True
        await env.db.flush()
        await env.mcp(actor, "publish_blog_post", page_id, force=True)

        assert denied == _DENIED
        assert (await env.snapshot(page_id))["is_public"] is True

    async def test_mcp_revoking_the_flag_blocks_the_same_token(self, env):
        actor = await env.actor("owner", superuser=False, flag=True)
        page_id = await env.page(public=True)
        await env.mcp(actor, "set_blog_post_meta", page_id, description="Opis przed odebraniem")

        actor.user.can_edit_blog = False
        await env.db.flush()
        message = await env.mcp_error(actor, "unpublish_blog_post", page_id)

        assert message == _DENIED
        snapshot = await env.snapshot(page_id)
        assert (snapshot["is_public"], snapshot["description"]) == (True, "Opis przed odebraniem")

    async def test_v2_granting_the_flag_unblocks_the_same_token(self, env):
        actor = await env.actor("owner", superuser=False, flag=False)
        page_id = await env.page(public=False)
        publish = next(op for op in OPS if op.name == "publish")
        denied = await env.v2(actor, publish, page_id)

        actor.user.can_edit_blog = True
        await env.db.flush()
        allowed = await env.v2(actor, publish, page_id)

        _assert_denied_problem(denied)
        assert allowed.status_code == 200
        assert (await env.snapshot(page_id))["is_public"] is True

    async def test_v2_revoking_the_flag_blocks_the_same_token(self, env):
        actor = await env.actor("owner", superuser=False, flag=True)
        page_id = await env.page(public=True)
        unpublish = next(op for op in OPS if op.name == "unpublish")

        actor.user.can_edit_blog = False
        await env.db.flush()
        response = await env.v2(actor, unpublish, page_id)

        _assert_denied_problem(response)
        assert (await env.snapshot(page_id))["is_public"] is True

    async def test_revoking_a_superuser_without_flag_blocks_writes(self, env):
        actor = await env.actor("member", superuser=True, flag=False)
        page_id = await env.page(public=False)
        await env.mcp(actor, "publish_blog_post", page_id, force=True)

        actor.user.is_superuser = False
        await env.db.flush()
        message = await env.mcp_error(actor, "unpublish_blog_post", page_id)

        assert message == _DENIED
        assert (await env.snapshot(page_id))["is_public"] is True


class TestReadsStayOpenWithoutPermission:
    async def test_v2_blog_reads_work_for_account_without_flag(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False)
        base = f"/api/v2/projects/{env.project.slug}/blog/posts"

        listing = await env.client.get(base, headers=actor.headers)
        detail = await env.client.get(f"{base}/{page_id}", headers=actor.headers)
        lint = await env.client.get(f"{base}/{page_id}/lint", headers=actor.headers)

        assert [listing.status_code, detail.status_code, lint.status_code] == [200, 200, 200]
        assert str(page_id) in {item["page_id"] for item in listing.json()["items"]}

    async def test_mcp_blog_reads_work_for_account_without_flag(self, env):
        actor = await env.actor("member", superuser=False, flag=False)
        page_id = await env.page(public=False)

        listing = await mcp_server.list_blog_posts(_mcp_ctx(actor), env.project.slug)
        detail = await env.mcp(actor, "get_blog_post", page_id)

        assert str(page_id) in {str(item["page_id"]) for item in listing}
        assert str(detail["page_id"]) == str(page_id)
