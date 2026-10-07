"""Testy integracyjne narzedzi MCP Scrum."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from monolynx import mcp_server
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token, hash_token

# ---------------------------------------------------------------------------
# Helper lokalny dla testow MCP wiki is_public (MON-203) -- ten sam wzorzec co
# _mcp_ctx w tests/integration/test_e2e_work_plan.py (regresja MON-71), zduplikowany
# lokalnie zgodnie z konwencja repo (kazdy plik testowy MCP ma wlasna kopie, patrz
# np. _make_ctx w tests/unit/test_mcp_server.py).
# ---------------------------------------------------------------------------


def _mcp_ctx(token: str) -> MagicMock:
    """Mock MCP Context z Bearer token (realny token z bazy)."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


@pytest.fixture
async def mcp_setup(db_session):
    """Setup: user + token + project + membership."""
    user = User(
        email=f"mcp-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()

    raw_token, token_hash = generate_api_token()
    api_token = UserApiToken(
        user_id=user.id,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        name="Test MCP Token",
    )
    db_session.add(api_token)

    _slug = f"mcp-test-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="MCP Test Project",
        slug=_slug,
        code=_slug.replace("-", "").upper()[:5],
        api_key=f"key-{uuid.uuid4().hex}",
    )
    db_session.add(project)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()

    return user, raw_token, project


@pytest.mark.integration
class TestMcpAuth:
    async def test_verify_valid_token(self, db_session, mcp_setup):
        """verify_mcp_token z prawidlowym tokenem -- token istnieje w DB."""
        _user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(select(UserApiToken).where(UserApiToken.token_hash == hashed))
        token_obj = result.scalar_one_or_none()
        assert token_obj is not None
        assert token_obj.is_active is True

    async def test_token_belongs_to_user(self, db_session, mcp_setup):
        """Token jest powiazany z wlasciwym userem."""
        user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(select(UserApiToken).where(UserApiToken.token_hash == hashed))
        token_obj = result.scalar_one()
        assert token_obj.user_id == user.id

    async def test_inactive_token_not_found(self, db_session, mcp_setup):
        """Dezaktywowany token nie powinien byc uznany za aktywny."""
        _user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(
            select(UserApiToken).where(
                UserApiToken.token_hash == hashed,
                UserApiToken.is_active.is_(True),
            )
        )
        token_obj = result.scalar_one()
        token_obj.is_active = False
        await db_session.flush()

        result2 = await db_session.execute(
            select(UserApiToken).where(
                UserApiToken.token_hash == hashed,
                UserApiToken.is_active.is_(True),
            )
        )
        assert result2.scalar_one_or_none() is None


@pytest.mark.integration
class TestTicketAiFlag:
    async def test_ticket_created_via_ai(self, db_session, mcp_setup):
        """Ticket z created_via_ai=True poprawnie sie zapisuje."""
        _user, _, project = mcp_setup
        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="AI Ticket",
            created_via_ai=True,
        )
        db_session.add(ticket)
        await db_session.flush()

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        t = result.scalar_one()
        assert t.created_via_ai is True

    async def test_ticket_default_not_ai(self, db_session, mcp_setup):
        """Domyslnie created_via_ai jest False."""
        _user, _, project = mcp_setup
        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Normal Ticket",
        )
        db_session.add(ticket)
        await db_session.flush()

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        t = result.scalar_one()
        assert t.created_via_ai is False


@pytest.mark.integration
class TestProjectMembership:
    async def test_user_is_member(self, db_session, mcp_setup):
        """User z mcp_setup jest memberem projektu."""
        user, _, project = mcp_setup
        result = await db_session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = result.scalar_one_or_none()
        assert member is not None
        assert member.role == "owner"

    async def test_non_member_not_found(self, db_session, mcp_setup):
        """Obcy user nie jest memberem projektu."""
        _, _, project = mcp_setup
        other_user = User(
            email=f"other-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
        )
        db_session.add(other_user)
        await db_session.flush()

        result = await db_session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == other_user.id,
            )
        )
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# MON-203: create_wiki_page / update_wiki_page (MCP) -- is_public + commit realny
#
# Kryterium 3: is_public domyslnie False, a zmiana widoczna po PONOWNYM ODCZYCIE z
# NOWEJ sesji (nie w tej samej sesji testowej -- outer-transaction rollback conftest.py
# by to zamaskowal, patrz .claude/rules/db-commit.md). Uzywamy realnego `engine` +
# async_sessionmaker podmienionego pod mcp_server.async_session_factory, analogicznie
# do test_mcp_schedule_ticket_persists_to_separate_session (regresja MON-71) w
# tests/integration/test_e2e_work_plan.py. Wiersze utrwalone poza transakcja db_session
# testowego -- czyszczone recznie w finally.
#
# Dzis create_wiki_page/update_wiki_page (mcp_server.py) NIE przyjmuja is_public --
# wywolanie z is_public=... rzuca TypeError. To jest zamierzone, TDD.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWikiPageIsPublicMcpCommit:
    """Kryterium 3 (MON-203)."""

    async def test_create_wiki_page_is_public_true_persists_to_separate_session(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

        uid = uuid.uuid4().hex[:8]
        raw_token, token_hash = generate_api_token()

        async with test_factory() as setup_db:
            user = User(email=f"mcp-pub-{uid}@test.com", is_superuser=False, can_edit_blog=True)
            setup_db.add(user)
            await setup_db.flush()
            setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon203-mcp-create"))
            project = Project(
                name=f"MCP Pub {uid}",
                slug=f"mcp-pub-{uid}",
                code=f"MP{uid[:4].upper()}",
                api_key=f"key-{uuid.uuid4().hex}",
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            await setup_db.commit()
            user_id = user.id
            project_id = project.id
            project_slug = project.slug

        ctx = _mcp_ctx(raw_token)

        try:
            with (
                patch("monolynx.services.wiki.upload_markdown", return_value=f"{project_slug}/mon203.md"),
                patch("monolynx.services.embeddings.update_page_embeddings"),
            ):
                created = await mcp_server.create_wiki_page(ctx, project_slug, "Strona MCP Publiczna MON203", "# Tresc", is_public=True)

            page_id = uuid.UUID(created["id"])

            # Odczyt w OSOBNEJ sesji -- lapie brak realnego db.commit() (wzorzec MON-71)
            async with test_factory() as check_db:
                result = await check_db.execute(select(WikiPage).where(WikiPage.id == page_id))
                page = result.scalar_one()
                assert page.is_public is True
        finally:
            async with test_factory() as cleanup_db:
                await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
                await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
                await cleanup_db.execute(delete(Project).where(Project.id == project_id))
                await cleanup_db.execute(delete(User).where(User.id == user_id))
                await cleanup_db.commit()

    async def test_create_wiki_page_omitted_is_public_defaults_to_false_persists_to_separate_session(self, engine, monkeypatch):
        """Kryterium 3 ('domyslnie False'): wywolanie BEZ parametru is_public ma dac
        strone z is_public=False, zweryfikowane odczytem w OSOBNEJ sesji (nie sama
        wartoscia zwrocona przez tool). Bez tego testu zmiana sygnatury na
        `is_public: bool = True` przeszlaby cala reszte pakietu MCP."""
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

        uid = uuid.uuid4().hex[:8]
        raw_token, token_hash = generate_api_token()

        async with test_factory() as setup_db:
            user = User(email=f"mcp-pub-def-{uid}@test.com", is_superuser=False)
            setup_db.add(user)
            await setup_db.flush()
            setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon203-mcp-default"))
            project = Project(
                name=f"MCP Pub Def {uid}",
                slug=f"mcp-pub-def-{uid}",
                code=f"MPD{uid[:3].upper()}",
                api_key=f"key-{uuid.uuid4().hex}",
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            await setup_db.commit()
            user_id = user.id
            project_id = project.id
            project_slug = project.slug

        ctx = _mcp_ctx(raw_token)

        try:
            with (
                patch("monolynx.services.wiki.upload_markdown", return_value=f"{project_slug}/mon203-def.md"),
                patch("monolynx.services.embeddings.update_page_embeddings"),
            ):
                # Brak kwarg is_public -- kryterium 3 wymaga domyslnie False.
                created = await mcp_server.create_wiki_page(ctx, project_slug, "Strona MCP Domyslna MON203", "# Tresc")

            page_id = uuid.UUID(created["id"])

            async with test_factory() as check_db:
                result = await check_db.execute(select(WikiPage).where(WikiPage.id == page_id))
                page = result.scalar_one()
                assert page.is_public is False
        finally:
            async with test_factory() as cleanup_db:
                await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
                await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
                await cleanup_db.execute(delete(Project).where(Project.id == project_id))
                await cleanup_db.execute(delete(User).where(User.id == user_id))
                await cleanup_db.commit()

    async def test_update_wiki_page_is_public_change_persists_to_separate_session(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

        uid = uuid.uuid4().hex[:8]
        raw_token, token_hash = generate_api_token()

        async with test_factory() as setup_db:
            user = User(email=f"mcp-pub-upd-{uid}@test.com", is_superuser=False, can_edit_blog=True)
            setup_db.add(user)
            await setup_db.flush()
            setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon203-mcp-update"))
            project = Project(
                name=f"MCP Pub Upd {uid}",
                slug=f"mcp-pub-upd-{uid}",
                code=f"MPU{uid[:3].upper()}",
                api_key=f"key-{uuid.uuid4().hex}",
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            page = WikiPage(
                project_id=project.id,
                title="Strona Do Publikacji MON203",
                slug="strona-do-publikacji-mon203",
                position=0,
                minio_path=f"{project.slug}/mon203-upd.md",
                is_ai_touched=False,
                is_public=False,
                created_by_id=user.id,
                last_edited_by_id=user.id,
            )
            setup_db.add(page)
            await setup_db.commit()
            user_id = user.id
            project_id = project.id
            project_slug = project.slug
            page_id = page.id

        ctx = _mcp_ctx(raw_token)

        try:
            # Sanity check przedwarunku: strona jest prywatna przed wywolaniem MCP
            async with test_factory() as pre_db:
                pre_result = await pre_db.execute(select(WikiPage).where(WikiPage.id == page_id))
                assert pre_result.scalar_one().is_public is False

            with patch("monolynx.services.embeddings.update_page_embeddings"):
                updated = await mcp_server.update_wiki_page(ctx, project_slug, str(page_id), is_public=True)
            assert updated["id"] == str(page_id)

            # Odczyt w OSOBNEJ sesji -- lapie brak realnego db.commit() (wzorzec MON-71)
            async with test_factory() as check_db:
                result = await check_db.execute(select(WikiPage).where(WikiPage.id == page_id))
                page_after = result.scalar_one()
                assert page_after.is_public is True
        finally:
            async with test_factory() as cleanup_db:
                await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
                await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
                await cleanup_db.execute(delete(Project).where(Project.id == project_id))
                await cleanup_db.execute(delete(User).where(User.id == user_id))
                await cleanup_db.commit()

    async def _assert_update_without_is_public_keeps_value(self, engine, monkeypatch, *, initial_is_public: bool) -> None:
        """Wspolna implementacja: update_wiki_page BEZ kwarg is_public (domyslnie None)
        nie zmienia istniejacej wartosci -- ani z True, ani z False. None znaczy
        'nie dotykaj', nie 'ustaw False' (kryterium 3, druga polowa wymogu Team Managera)."""
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

        uid = uuid.uuid4().hex[:8]
        raw_token, token_hash = generate_api_token()

        async with test_factory() as setup_db:
            user = User(email=f"mcp-pub-none-{uid}@test.com", is_superuser=False, can_edit_blog=True)
            setup_db.add(user)
            await setup_db.flush()
            setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon203-mcp-none"))
            project = Project(
                name=f"MCP Pub None {uid}",
                slug=f"mcp-pub-none-{uid}",
                code=f"MPN{uid[:3].upper()}",
                api_key=f"key-{uuid.uuid4().hex}",
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            page = WikiPage(
                project_id=project.id,
                title="Strona Bez Zmiany Flagi MON203",
                slug=f"strona-bez-zmiany-flagi-mon203-{uid}",
                position=0,
                minio_path=f"{project.slug}/mon203-none.md",
                is_ai_touched=False,
                is_public=initial_is_public,
                created_by_id=user.id,
                last_edited_by_id=user.id,
            )
            setup_db.add(page)
            await setup_db.commit()
            user_id = user.id
            project_id = project.id
            project_slug = project.slug
            page_id = page.id

        ctx = _mcp_ctx(raw_token)

        try:
            # Brak kwarg is_public -- domyslne None ma NIE zmieniac flagi, tylko tytul.
            updated = await mcp_server.update_wiki_page(ctx, project_slug, str(page_id), title="Nowy Tytul MON203")
            assert updated["id"] == str(page_id)

            async with test_factory() as check_db:
                result = await check_db.execute(select(WikiPage).where(WikiPage.id == page_id))
                page_after = result.scalar_one()
                assert page_after.is_public is initial_is_public
        finally:
            async with test_factory() as cleanup_db:
                await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
                await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
                await cleanup_db.execute(delete(Project).where(Project.id == project_id))
                await cleanup_db.execute(delete(User).where(User.id == user_id))
                await cleanup_db.commit()

    async def test_update_wiki_page_omitted_is_public_keeps_existing_true_value(self, engine, monkeypatch):
        await self._assert_update_without_is_public_keeps_value(engine, monkeypatch, initial_is_public=True)

    async def test_update_wiki_page_omitted_is_public_keeps_existing_false_value(self, engine, monkeypatch):
        await self._assert_update_without_is_public_keeps_value(engine, monkeypatch, initial_is_public=False)


@pytest.mark.integration
class TestSetBlogPostMetaMcpCommit:
    """MON-261: zapis `set_blog_post_meta` jest commitowany (wzorzec MON-71 / MON-203)."""

    @staticmethod
    async def _setup(test_factory, uid: str, *, can_edit_blog: bool = True):
        raw_token, token_hash = generate_api_token()
        async with test_factory() as setup_db:
            user = User(email=f"mcp-blog-{uid}@test.com", is_superuser=False, can_edit_blog=can_edit_blog)
            setup_db.add(user)
            await setup_db.flush()
            setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon261-blog-meta"))
            project = Project(
                name=f"MCP Blog {uid}",
                slug=f"mcp-blog-{uid}",
                code=f"MB{uid[:4].upper()}",
                api_key=f"key-{uuid.uuid4().hex}",
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            page = WikiPage(
                project_id=project.id,
                title=f"Wpis MON261 {uid}",
                slug=f"wpis-mon261-{uid}",
                position=0,
                minio_path=f"{project.slug}/{uid}.md",
                is_public=False,
                created_by_id=user.id,
                last_edited_by_id=user.id,
            )
            setup_db.add(page)
            await setup_db.flush()
            setup_db.add(BlogPost(page_id=page.id, slug=page.slug, description="Stary opis", tags=["a", "b"], author_name="Autor"))
            await setup_db.commit()
            return raw_token, user.id, project.id, project.slug, page.id

    @staticmethod
    async def _cleanup(test_factory, user_id, project_id):
        async with test_factory() as cleanup_db:
            await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.id == user_id))
            await cleanup_db.commit()

    async def test_set_blog_post_meta_is_visible_to_get_blog_post_in_new_session(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8])
        ctx = _mcp_ctx(raw_token)

        try:
            await mcp_server.set_blog_post_meta(ctx, project_slug, str(page_id), description="Nowy opis MON261", lang="en")

            with patch("monolynx.mcp_server.get_page_content", return_value="# Treść"):
                detail = await mcp_server.get_blog_post(ctx, project_slug, str(page_id))
            async with test_factory() as check_db:
                row = (await check_db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one()

            assert detail["description"] == "Nowy opis MON261"
            assert detail["lang"] == "en"
            assert detail["content"] == "# Treść"
            assert (row.description, row.lang) == ("Nowy opis MON261", "en")
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_omitted_tags_keep_tags_and_empty_list_clears_them_across_sessions(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8])
        ctx = _mcp_ctx(raw_token)

        async def _tags() -> list[str]:
            async with test_factory() as check_db:
                return list((await check_db.execute(select(BlogPost.tags).where(BlogPost.page_id == page_id))).scalar_one())

        try:
            await mcp_server.set_blog_post_meta(ctx, project_slug, str(page_id), description="Bez tagów w wywołaniu")
            assert await _tags() == ["a", "b"]

            await mcp_server.set_blog_post_meta(ctx, project_slug, str(page_id), tags=[])
            assert await _tags() == []
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_list_blog_posts_in_new_session_shows_saved_draft_state(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8])
        ctx = _mcp_ctx(raw_token)

        try:
            await mcp_server.set_blog_post_meta(ctx, project_slug, str(page_id), author_name="Nowy Autor")

            listed = await mcp_server.list_blog_posts(ctx, project_slug, status="draft")

            assert [i["page_id"] for i in listed] == [str(page_id)]
            assert listed[0]["author_name"] == "Nowy Autor"
            assert listed[0]["is_public"] is False
            assert listed[0]["public_url"] is None
        finally:
            await self._cleanup(test_factory, user_id, project_id)


@pytest.mark.integration
class TestBlogWriteDeniedWithoutFlagMcpPersistsNothing:
    """MON-270 (K1, K2, K7): konto bez `blog:write` dostaje odmowę, a stan odczytany w NOWEJ sesji się nie zmienia."""

    _setup = staticmethod(TestSetBlogPostMetaMcpCommit._setup)
    _cleanup = staticmethod(TestSetBlogPostMetaMcpCommit._cleanup)

    @staticmethod
    async def _state(test_factory, page_id):
        async with test_factory() as check_db:
            page = (await check_db.execute(select(WikiPage).where(WikiPage.id == page_id))).scalar_one()
            post = (await check_db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one()
            return page.is_public, page.title, post.description, post.tags, post.published_at

    @staticmethod
    async def _make_public(test_factory, page_id):
        async with test_factory() as db:
            page = (await db.execute(select(WikiPage).where(WikiPage.id == page_id))).scalar_one()
            page.is_public = True
            await db.commit()

    async def test_set_blog_post_meta_is_denied_and_row_is_unchanged(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=False)
        before = await self._state(test_factory, page_id)

        try:
            with pytest.raises(ValueError) as exc_info:
                await mcp_server.set_blog_post_meta(_mcp_ctx(raw_token), project_slug, str(page_id), description="Nowy opis", lang="en")

            assert str(exc_info.value) == "Brak uprawnienia blog:write"
            assert await self._state(test_factory, page_id) == before
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_publish_blog_post_is_denied_and_page_stays_private(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=False)
        before = await self._state(test_factory, page_id)

        try:
            with (
                patch("monolynx.mcp_server.get_page_content", return_value="# Szkic\n\nTreść bez TLDR."),
                pytest.raises(ValueError) as exc_info,
            ):
                await mcp_server.publish_blog_post(_mcp_ctx(raw_token), project_slug, str(page_id), force=True)

            assert str(exc_info.value) == "Brak uprawnienia blog:write"
            assert await self._state(test_factory, page_id) == before
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_unpublish_blog_post_is_denied_and_page_stays_public(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=False)
        await self._make_public(test_factory, page_id)
        before = await self._state(test_factory, page_id)

        try:
            with pytest.raises(ValueError) as exc_info:
                await mcp_server.unpublish_blog_post(_mcp_ctx(raw_token), project_slug, str(page_id))

            assert str(exc_info.value) == "Brak uprawnienia blog:write"
            assert await self._state(test_factory, page_id) == before
            assert before[0] is True
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_update_wiki_page_is_public_is_denied_but_content_edit_goes_through(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=False)
        before = await self._state(test_factory, page_id)
        ctx = _mcp_ctx(raw_token)

        try:
            with (
                patch("monolynx.services.wiki.upload_markdown", return_value=f"{project_slug}/x.md") as upload_mock,
                patch("monolynx.services.embeddings.update_page_embeddings"),
            ):
                with pytest.raises(ValueError) as exc_info:
                    await mcp_server.update_wiki_page(ctx, project_slug, str(page_id), title="Zmieniony", content="# Nowa", is_public=True)

                assert str(exc_info.value) == "Brak uprawnienia blog:write"
                upload_mock.assert_not_called()
                assert await self._state(test_factory, page_id) == before

                result = await mcp_server.update_wiki_page(ctx, project_slug, str(page_id), title="Zmieniony po cichu")

            assert result["is_public"] is False
            after = await self._state(test_factory, page_id)
            assert (after[0], after[1]) == (False, "Zmieniony po cichu")
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_create_wiki_page_is_public_is_denied_and_creates_nothing(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, _page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=False)
        title = f"Publiczna bez prawa {uuid.uuid4().hex[:6]}"

        try:
            with (
                patch("monolynx.services.wiki.upload_markdown", return_value=f"{project_slug}/y.md") as upload_mock,
                pytest.raises(ValueError) as exc_info,
            ):
                await mcp_server.create_wiki_page(_mcp_ctx(raw_token), project_slug, title, "# Treść", is_public=True)

            assert str(exc_info.value) == "Brak uprawnienia blog:write"
            upload_mock.assert_not_called()
            async with test_factory() as check_db:
                assert (await check_db.execute(select(WikiPage.id).where(WikiPage.project_id == project_id, WikiPage.title == title))).first() is None
        finally:
            await self._cleanup(test_factory, user_id, project_id)

    async def test_account_with_flag_publishes_through_the_same_call(self, engine, monkeypatch):
        test_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)
        raw_token, user_id, project_id, project_slug, page_id = await self._setup(test_factory, uuid.uuid4().hex[:8], can_edit_blog=True)

        try:
            with patch("monolynx.mcp_server.get_page_content", return_value="# Szkic\n\nTreść bez TLDR."):
                result = await mcp_server.publish_blog_post(_mcp_ctx(raw_token), project_slug, str(page_id), force=True)

            assert result["is_public"] is True
            assert (await self._state(test_factory, page_id))[0] is True
        finally:
            await self._cleanup(test_factory, user_id, project_id)
