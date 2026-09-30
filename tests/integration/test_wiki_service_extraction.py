"""Testy integracyjne -- services/wiki.py (MON-139: wyekstrahowana logika MCP tooli Wiki).

Testy wolaja funkcje serwisu bezposrednio z fixture `db_session`, bez przechodzenia
przez wrappery MCP (te maja wlasne testy w tests/unit/test_mcp_server.py, ktorych NIE
wolno zmieniac). Kontrakt funkcji (nazwy, sygnatury, dataclassy, komunikaty bledow) wg
KONTRAKT API ustalonego przez Team Managera -- patrz tresc ticketu MON-139.

Pokrycie: list_pages_flat (pre-order DFS, depth), get_wiki_page_detail (pola, ValueError
dla obcego projektu), get_wiki_config (id stron systemowych, izolacja projektow),
set_wiki_config (RBAC settings:write, commit widoczny po odczycie).
"""

from __future__ import annotations

import re
import secrets
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import wiki as svc
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, tag: str) -> User:
    user = User(email=f"wk_svc_{tag}_{uuid.uuid4().hex[:8]}@x.com", password_hash=hash_password("x"))
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"Wiki Svc {tag}",
        slug=f"wk-svc-{tag}-{uuid.uuid4().hex[:6]}",
        code=f"W{secrets.token_hex(3).upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_member(db, project: Project, user: User, role: str = "owner") -> ProjectMember:
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db.add(member)
    await db.flush()
    return member


async def _setup(db, tag: str, role: str = "owner"):
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    await _make_member(db, project, user, role=role)
    return user, project


def _make_page(
    project: Project,
    user: User,
    *,
    slug: str,
    title: str,
    parent_id: uuid.UUID | None = None,
    position: int = 0,
    minio_path: str | None = None,
    last_edited_by: User | None = None,
) -> WikiPage:
    """Buduje WikiPage bez zapisu do DB -- caller musi db.add() + flush()."""
    editor = last_edited_by or user
    return WikiPage(
        id=uuid.uuid4(),
        project_id=project.id,
        parent_id=parent_id,
        title=title,
        slug=slug,
        position=position,
        minio_path=minio_path or f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        created_by_id=user.id,
        last_edited_by_id=editor.id,
    )


# ---------------------------------------------------------------------------
# list_pages_flat -- pre-order DFS
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListPagesFlat:
    async def test_preorder_dfs_with_depth_two_level_tree(self, db_session):
        """Drzewo: 2 korzenie, kazdy z 1 dzieckiem. DFS preorder != BFS level-order dla tego
        ksztaltu (BFS dalby oba korzenie przed dowolnym dzieckiem) -- zabija mutanta
        zamieniajacego rekursje DFS na plaski obieg poziomami. Explicit `position` ustala
        kolejnosc korzeni i dzieci niezaleznie od kolejnosci INSERT do bazy."""
        user, project = await _setup(db_session, "flat-tree")

        root_a = _make_page(project, user, slug="a-root", title="A Root", position=0)
        root_b = _make_page(project, user, slug="b-root", title="B Root", position=1)
        # Insert w kolejnosci ROZNEJ od oczekiwanego preorder (B1 przed A1, root_b przed root_a)
        # -- gdyby serwis polegal na kolejnosci INSERT zamiast position/tree, test by to zlapal.
        child_b1 = _make_page(project, user, slug="b1-child", title="B1 Child", parent_id=root_b.id, position=0)
        db_session.add_all([child_b1, root_b, root_a])
        child_a1 = _make_page(project, user, slug="a1-child", title="A1 Child", parent_id=root_a.id, position=0)
        db_session.add(child_a1)
        await db_session.flush()

        result = await svc.list_pages_flat(db_session, user, project)

        assert [r.title for r in result] == ["A Root", "A1 Child", "B Root", "B1 Child"]
        assert [r.depth for r in result] == [0, 1, 0, 1]

    async def test_empty_project_returns_empty_list(self, db_session):
        user, project = await _setup(db_session, "flat-empty")

        result = await svc.list_pages_flat(db_session, user, project)

        assert result == []

    async def test_isolated_by_project(self, db_session):
        """Strona z INNEGO projektu nie moze przeciec do wyniku."""
        user, project = await _setup(db_session, "flat-iso")
        _other_user, other_project = await _setup(db_session, "flat-iso-other")
        other_page = _make_page(other_project, _other_user, slug="obcy", title="Obca strona")
        db_session.add(other_page)
        await db_session.flush()

        result = await svc.list_pages_flat(db_session, user, project)

        assert result == []


# ---------------------------------------------------------------------------
# get_wiki_page_detail
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetWikiPageDetail:
    @patch("monolynx.services.wiki.get_markdown")
    async def test_returns_full_detail_with_author_emails(self, mock_get_markdown, db_session):
        mock_get_markdown.return_value = "# Tresc strony"
        creator, project = await _setup(db_session, "detail-full")
        editor = await _make_user(db_session, "detail-full-editor")
        await _make_member(db_session, project, editor, role="member")

        page = _make_page(
            project,
            creator,
            slug="dziecko",
            title="Strona dziecko",
            position=3,
            last_edited_by=editor,
        )
        parent = _make_page(project, creator, slug="rodzic", title="Rodzic")
        db_session.add(parent)
        await db_session.flush()
        page.parent_id = parent.id
        db_session.add(page)
        await db_session.flush()

        result = await svc.get_wiki_page_detail(db_session, creator, project, str(page.id))

        assert result.id == page.id
        assert result.title == "Strona dziecko"
        assert result.slug == "dziecko"
        assert result.parent_id == parent.id
        assert result.position == 3
        assert result.content == "# Tresc strony"
        assert result.is_ai_touched is False
        assert result.created_by == creator.email
        assert result.last_edited_by == editor.email
        assert result.created_at is not None
        assert result.updated_at is not None
        mock_get_markdown.assert_called_once_with(page.minio_path)

    @patch("monolynx.services.wiki.get_markdown")
    async def test_root_page_has_none_parent_id(self, mock_get_markdown, db_session):
        mock_get_markdown.return_value = "tresc"
        user, project = await _setup(db_session, "detail-root")
        page = _make_page(project, user, slug="korzen", title="Korzen")
        db_session.add(page)
        await db_session.flush()

        result = await svc.get_wiki_page_detail(db_session, user, project, str(page.id))

        assert result.parent_id is None

    async def test_page_from_other_project_raises(self, db_session):
        user, project = await _setup(db_session, "detail-cross")
        other_user, other_project = await _setup(db_session, "detail-cross-other")
        other_page = _make_page(other_project, other_user, slug="obca", title="Obca")
        db_session.add(other_page)
        await db_session.flush()

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.get_wiki_page_detail(db_session, user, project, str(other_page.id))

    async def test_nonexistent_page_raises(self, db_session):
        user, project = await _setup(db_session, "detail-missing")

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.get_wiki_page_detail(db_session, user, project, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# get_wiki_config
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetWikiConfig:
    async def test_no_system_pages_returns_none_ids(self, db_session):
        user, project = await _setup(db_session, "cfg-empty")

        result = await svc.get_wiki_config(db_session, user, project)

        assert result.wiki_llm_enabled is False
        assert result.index_page_id is None
        assert result.log_page_id is None
        assert result.schema_page_id is None

    async def test_with_system_pages_returns_their_ids(self, db_session):
        user, project = await _setup(db_session, "cfg-full")
        project.wiki_llm_enabled = True
        index_page = _make_page(project, user, slug="wiki-index", title="Indeks Wiki")
        log_page = _make_page(project, user, slug="wiki-log", title="Dziennik Wiki")
        schema_page = _make_page(project, user, slug="wiki-schema", title="Schemat Wiki")
        db_session.add_all([index_page, log_page, schema_page])
        await db_session.flush()

        result = await svc.get_wiki_config(db_session, user, project)

        assert result.wiki_llm_enabled is True
        assert result.index_page_id == index_page.id
        assert result.log_page_id == log_page.id
        assert result.schema_page_id == schema_page.id

    async def test_system_page_from_other_project_does_not_leak(self, db_session):
        user, project = await _setup(db_session, "cfg-iso")
        other_user, other_project = await _setup(db_session, "cfg-iso-other")
        other_index = _make_page(other_project, other_user, slug="wiki-index", title="Obcy indeks")
        db_session.add(other_index)
        await db_session.flush()

        result = await svc.get_wiki_config(db_session, user, project)

        assert result.index_page_id is None


# ---------------------------------------------------------------------------
# set_wiki_config -- RBAC settings:write + commit
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSetWikiConfig:
    async def test_owner_enables_and_change_is_committed(self, db_session):
        """Sprawdza DWIE rzeczy: (1) commit() faktycznie wywolany -- spy zamiast odczytu z
        osobnego polaczenia, bo db_session dziala w otwartej zewnetrznej transakcji z
        rollbackiem (.claude/rules/test-kills-mutant.md #7); (2) flaga rzeczywiscie
        ustawiona na rekordzie po ponownym (expire'owanym) odczycie z tej samej sesji --
        lapie mutanta, ktory commituje ale nie ustawia atrybutu."""
        user, project = await _setup(db_session, "cfg-set-ok", role="owner")
        assert project.wiki_llm_enabled is False

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            result = await svc.set_wiki_config(db_session, user, project, enabled=True)

        commit_spy.assert_awaited()
        assert result.wiki_llm_enabled is True

        # project_id zapamietany PRZED expire_all() -- po expire_all() kazdy dostep do
        # atrybutu ORM (w tym .id) wymaga odswiezenia synchronicznym lazy loadem, co w
        # kontekscie async rzuca MissingGreenlet.
        project_id = project.id
        db_session.expire_all()
        reread = (await db_session.execute(select(Project.wiki_llm_enabled).where(Project.id == project_id))).scalar_one()
        assert reread is True

    async def test_member_without_settings_write_raises_and_flag_unchanged(self, db_session):
        user, project = await _setup(db_session, "cfg-set-noperm", role="member")
        project_id = project.id

        with pytest.raises(ValueError, match=re.escape("Brak uprawnienia do zmiany konfiguracji projektu (settings:write)")):
            await svc.set_wiki_config(db_session, user, project, enabled=True)

        db_session.expire_all()
        reread = (await db_session.execute(select(Project.wiki_llm_enabled).where(Project.id == project_id))).scalar_one()
        assert reread is False
