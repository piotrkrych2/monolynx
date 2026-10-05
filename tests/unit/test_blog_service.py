"""Testy serwisu services/blog.py (MON-254) -- BlogPost: metadane wpisu 1:1 ze stroną wiki.

Używa realnej bazy (db_session z rollbackiem), bez mockowania ORM. Fixture db_session
opakowuje test w jedną transakcję, więc sam nie wykryje braku commita: test commita
robi spy na `commit` (wzorzec tests/integration/test_projects_service.py), a test
`TestSetPostMetaPersistence` czyta wynik w NOWEJ sesji z realnego `engine`.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_page import WikiPage
from monolynx.services import blog as svc

pytestmark = pytest.mark.unit


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_user(db, tag: str) -> User:
    user = User(email=f"blogsvc-{tag}-{_uid()}@example.com", is_superuser=False)
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"Blog svc {tag}",
        slug=f"blogsvc-{tag}-{_uid()}",
        code="B" + secrets.token_hex(4).upper(),
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


async def _setup(db, tag: str) -> tuple[User, Project]:
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    await _make_member(db, project, user)
    return user, project


async def _make_page(db, project: Project, user: User, *, slug: str | None = None, is_public: bool = False) -> WikiPage:
    page_slug = slug or f"strona-{_uid()}"
    page = WikiPage(
        project_id=project.id,
        title=page_slug,
        slug=page_slug,
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    db.add(page)
    await db.flush()
    return page


async def _make_post(db, page: WikiPage, **fields) -> BlogPost:
    values = {"slug": page.slug, **fields}
    post = BlogPost(page_id=page.id, **values)
    db.add(post)
    await db.flush()
    return post


async def _make_attachment(db, page: WikiPage) -> WikiAttachment:
    att = WikiAttachment(
        wiki_page_id=page.id,
        filename="cover.png",
        storage_path=f"wiki-attachments/{page.id}/cover.png",
        mime_type="image/png",
        size=10,
    )
    db.add(att)
    await db.flush()
    return att


async def _post_row(db, page_id: uuid.UUID) -> BlogPost | None:
    return (await db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one_or_none()


class TestEnsurePost:
    async def test_creates_row_with_defaults_from_page_slug(self, db_session):
        user, project = await _setup(db_session, "ens-def")
        page = await _make_page(db_session, project, user, is_public=False)

        post = await svc.ensure_post(db_session, page)

        assert post.page_id == page.id
        assert post.slug == page.slug
        assert post.lang == "pl"
        assert post.author_name == "Zespół Monolynx"
        assert post.description == ""
        assert post.tags == []
        assert post.previous_slugs == []
        assert post.translation_of_id is None
        assert post.cover_attachment_id is None
        assert post.last_verified_at is None

    async def test_public_page_gets_published_at_now(self, db_session):
        user, project = await _setup(db_session, "ens-pub")
        page = await _make_page(db_session, project, user, is_public=True)
        before = datetime.now(UTC)

        post = await svc.ensure_post(db_session, page)

        assert post.published_at is not None
        assert before <= post.published_at <= datetime.now(UTC)

    async def test_private_page_has_no_published_at(self, db_session):
        user, project = await _setup(db_session, "ens-priv")
        page = await _make_page(db_session, project, user, is_public=False)

        post = await svc.ensure_post(db_session, page)

        assert post.published_at is None

    async def test_is_idempotent_and_returns_same_row(self, db_session):
        user, project = await _setup(db_session, "ens-idem")
        page = await _make_page(db_session, project, user, is_public=True)

        first = await svc.ensure_post(db_session, page)
        second = await svc.ensure_post(db_session, page)

        rows = (await db_session.execute(select(BlogPost).where(BlogPost.page_id == page.id))).scalars().all()
        assert len(rows) == 1
        assert first.page_id == second.page_id
        assert second.published_at == first.published_at

    async def test_does_not_overwrite_slug_after_page_slug_change(self, db_session):
        user, project = await _setup(db_session, "ens-slug")
        page = await _make_page(db_session, project, user, is_public=True)
        post = await svc.ensure_post(db_session, page)
        original_slug = post.slug
        page.slug = f"nowy-tytul-{_uid()}"
        await db_session.flush()

        again = await svc.ensure_post(db_session, page)

        assert again.slug == original_slug
        assert again.previous_slugs == []

    async def test_publishing_previously_private_page_sets_published_at(self, db_session):
        user, project = await _setup(db_session, "ens-publish")
        page = await _make_page(db_session, project, user, is_public=False)
        post = await svc.ensure_post(db_session, page)
        assert post.published_at is None
        page.is_public = True
        await db_session.flush()

        again = await svc.ensure_post(db_session, page)

        assert again.published_at is not None

    async def test_does_not_reset_existing_published_at(self, db_session):
        user, project = await _setup(db_session, "ens-keep-date")
        page = await _make_page(db_session, project, user, is_public=True)
        original = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        await _make_post(db_session, page, published_at=original)

        post = await svc.ensure_post(db_session, page)

        assert post.published_at == original

    async def test_slug_taken_by_other_post_raises(self, db_session):
        user, project = await _setup(db_session, "ens-coll")
        other_project = await _make_project(db_session, "ens-coll-other")
        shared = f"wspolny-{_uid()}"
        owner_page = await _make_page(db_session, project, user, slug=shared)
        await _make_post(db_session, owner_page)
        page = await _make_page(db_session, other_project, user, slug=shared)

        with pytest.raises(ValueError, match=shared):
            await svc.ensure_post(db_session, page)

    async def test_slug_in_other_posts_previous_slugs_raises(self, db_session):
        user, project = await _setup(db_session, "ens-prev")
        old = f"stary-{_uid()}"
        owner_page = await _make_page(db_session, project, user)
        await _make_post(db_session, owner_page, previous_slugs=[old])
        page = await _make_page(db_session, project, user, slug=old)

        with pytest.raises(ValueError, match=old):
            await svc.ensure_post(db_session, page)

    async def test_does_not_commit(self, db_session):
        """Kontrakt: ensure_post działa w transakcji wołającego (create/update_wiki_page commitują po nim)."""
        user, project = await _setup(db_session, "ens-nocommit")
        page = await _make_page(db_session, project, user, is_public=True)
        spy = AsyncMock(side_effect=db_session.commit)

        with patch.object(db_session, "commit", spy):
            await svc.ensure_post(db_session, page)

        spy.assert_not_awaited()


class TestSetPostMetaValidation:
    async def test_description_over_300_chars_rejected(self, db_session):
        user, project = await _setup(db_session, "val-desc301")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, description="a" * 301)

    async def test_description_of_300_chars_accepted(self, db_session):
        user, project = await _setup(db_session, "val-desc300")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, description="a" * 300)

        assert post.description == "a" * 300

    async def test_description_is_stripped_before_length_check(self, db_session):
        user, project = await _setup(db_session, "val-descstrip")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, description="  " + "a" * 300 + "  ")

        assert post.description == "a" * 300

    async def test_lang_outside_pl_en_rejected(self, db_session):
        user, project = await _setup(db_session, "val-lang")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, lang="de")

    async def test_lang_en_accepted(self, db_session):
        user, project = await _setup(db_session, "val-lang-en")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, lang="en")

        assert post.lang == "en"

    async def test_tag_with_invalid_format_rejected(self, db_session):
        user, project = await _setup(db_session, "val-tagfmt")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, tags=["ok", "Zły Tag"])

    async def test_tag_longer_than_50_chars_rejected(self, db_session):
        user, project = await _setup(db_session, "val-taglen")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, tags=["a" * 51])

    async def test_tag_of_50_chars_accepted(self, db_session):
        user, project = await _setup(db_session, "val-taglen50")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, tags=["a" * 50])

        assert post.tags == ["a" * 50]

    async def test_more_than_10_tags_rejected(self, db_session):
        user, project = await _setup(db_session, "val-tagcount")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, tags=[f"t{i}" for i in range(11)])

    async def test_exactly_10_tags_accepted(self, db_session):
        user, project = await _setup(db_session, "val-tagcount10")
        page = await _make_page(db_session, project, user)
        tags = [f"t{i}" for i in range(10)]

        post = await svc.set_post_meta(db_session, user, project, page.id, tags=tags)

        assert post.tags == tags

    async def test_duplicate_tags_removed_keeping_first_occurrence_order(self, db_session):
        user, project = await _setup(db_session, "val-tagdup")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, tags=["zeta", "alfa", "zeta", "beta", "alfa"])

        assert post.tags == ["zeta", "alfa", "beta"]

    async def test_duplicates_count_once_toward_10_tag_limit(self, db_session):
        """11 elementów, ale 10 unikalnych: limit liczy się po deduplikacji."""
        user, project = await _setup(db_session, "val-tagdup-limit")
        page = await _make_page(db_session, project, user)
        tags = [f"t{i}" for i in range(10)] + ["t0"]

        post = await svc.set_post_meta(db_session, user, project, page.id, tags=tags)

        assert post.tags == [f"t{i}" for i in range(10)]

    async def test_cover_from_other_page_rejected(self, db_session):
        user, project = await _setup(db_session, "val-cover-other")
        page = await _make_page(db_session, project, user)
        other_page = await _make_page(db_session, project, user)
        foreign = await _make_attachment(db_session, other_page)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, cover_attachment_id=foreign.id)

    async def test_cover_from_same_page_accepted(self, db_session):
        user, project = await _setup(db_session, "val-cover-own")
        page = await _make_page(db_session, project, user)
        own = await _make_attachment(db_session, page)

        post = await svc.set_post_meta(db_session, user, project, page.id, cover_attachment_id=own.id)

        assert post.cover_attachment_id == own.id

    async def test_cover_none_clears_cover(self, db_session):
        user, project = await _setup(db_session, "val-cover-clear")
        page = await _make_page(db_session, project, user)
        own = await _make_attachment(db_session, page)
        await svc.set_post_meta(db_session, user, project, page.id, cover_attachment_id=own.id)

        post = await svc.set_post_meta(db_session, user, project, page.id, cover_attachment_id=None)

        assert post.cover_attachment_id is None

    async def test_nonexistent_cover_rejected(self, db_session):
        user, project = await _setup(db_session, "val-cover-missing")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, cover_attachment_id=uuid.uuid4())

    async def test_translation_of_self_rejected(self, db_session):
        user, project = await _setup(db_session, "val-trans-self")
        page = await _make_page(db_session, project, user)
        await svc.ensure_post(db_session, page)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, translation_of_id=page.id)

    async def test_translation_of_nonexistent_post_rejected(self, db_session):
        user, project = await _setup(db_session, "val-trans-missing")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, translation_of_id=uuid.uuid4())

    async def test_translation_of_existing_post_accepted(self, db_session):
        user, project = await _setup(db_session, "val-trans-ok")
        original_page = await _make_page(db_session, project, user)
        await _make_post(db_session, original_page)
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, translation_of_id=original_page.id, lang="en")

        assert post.translation_of_id == original_page.id

    async def test_translation_of_none_clears_link(self, db_session):
        user, project = await _setup(db_session, "val-trans-clear")
        original_page = await _make_page(db_session, project, user)
        await _make_post(db_session, original_page)
        page = await _make_page(db_session, project, user)
        await svc.set_post_meta(db_session, user, project, page.id, translation_of_id=original_page.id)

        post = await svc.set_post_meta(db_session, user, project, page.id, translation_of_id=None)

        assert post.translation_of_id is None

    async def test_empty_author_name_rejected(self, db_session):
        user, project = await _setup(db_session, "val-author")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, author_name="   ")

    async def test_author_name_is_saved(self, db_session):
        user, project = await _setup(db_session, "val-author-ok")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, author_name="Anna Nowak")

        assert post.author_name == "Anna Nowak"

    async def test_author_name_longer_than_255_rejected(self, db_session):
        user, project = await _setup(db_session, "val-author-256")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError, match="255"):
            await svc.set_post_meta(db_session, user, project, page.id, author_name="a" * 256)

    async def test_author_name_of_exactly_255_chars_accepted(self, db_session):
        user, project = await _setup(db_session, "val-author-255")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, author_name="a" * 255)

        assert post.author_name == "a" * 255

    async def test_author_name_is_stripped_before_saving(self, db_session):
        user, project = await _setup(db_session, "val-author-strip")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, author_name="  Anna Nowak  ")

        assert post.author_name == "Anna Nowak"

    async def test_author_name_length_limit_applies_after_strip(self, db_session):
        """255 znaków otoczone spacjami mieści się w limicie (długość liczona po strip)."""
        user, project = await _setup(db_session, "val-author-strip-255")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, author_name="  " + "a" * 255 + "  ")

        assert post.author_name == "a" * 255

    async def test_slug_with_invalid_format_rejected(self, db_session):
        user, project = await _setup(db_session, "val-slugfmt")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, slug="Zły_Slug")

    async def test_slug_longer_than_255_chars_rejected(self, db_session):
        user, project = await _setup(db_session, "val-sluglen")
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, slug="a" * 256)


class TestSetPostMetaPermissionAndScope:
    async def test_role_without_wiki_write_gets_exact_message_and_no_write(self, db_session):
        user = await _make_user(db_session, "perm")
        project = await _make_project(db_session, "perm")
        role = Role(name=f"read-only-{_uid()}", project_id=project.id, permissions={"wiki": ["read"]}, is_system=False)
        db_session.add(role)
        await db_session.flush()
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="member", role_id=role.id))
        await db_session.flush()
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError) as exc_info:
            await svc.set_post_meta(db_session, user, project, page.id, description="nie powinno się zapisać")

        assert str(exc_info.value) == "Brak uprawnienia wiki:write"
        assert await _post_row(db_session, page.id) is None

    async def test_permission_is_checked_before_page_lookup(self, db_session):
        """Rola bez wiki:write dla nieistniejącej strony dostaje 403-owy komunikat, nie 'nie istnieje'."""
        user = await _make_user(db_session, "perm-first")
        project = await _make_project(db_session, "perm-first")
        role = Role(name=f"read-only-{_uid()}", project_id=project.id, permissions={"wiki": ["read"]}, is_system=False)
        db_session.add(role)
        await db_session.flush()
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="member", role_id=role.id))
        await db_session.flush()

        with pytest.raises(ValueError, match=r"^Brak uprawnienia wiki:write$"):
            await svc.set_post_meta(db_session, user, project, uuid.uuid4(), description="x")

    async def test_page_from_other_project_rejected(self, db_session):
        user, project = await _setup(db_session, "scope")
        other_project = await _make_project(db_session, "scope-other")
        foreign_page = await _make_page(db_session, other_project, user)

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.set_post_meta(db_session, user, project, foreign_page.id, description="x")

        assert await _post_row(db_session, foreign_page.id) is None

    async def test_nonexistent_page_rejected(self, db_session):
        user, project = await _setup(db_session, "scope-missing")

        with pytest.raises(ValueError, match=r"^Strona wiki nie istnieje$"):
            await svc.set_post_meta(db_session, user, project, uuid.uuid4(), description="x")


class TestSetPostMetaBehavior:
    async def test_creates_missing_row_for_private_page_without_publishing(self, db_session):
        user, project = await _setup(db_session, "beh-create")
        page = await _make_page(db_session, project, user, is_public=False)

        post = await svc.set_post_meta(db_session, user, project, page.id, description="opis")

        assert post.page_id == page.id
        assert post.published_at is None
        assert page.is_public is False

    async def test_does_not_change_page_is_public(self, db_session):
        user, project = await _setup(db_session, "beh-ispublic")
        page = await _make_page(db_session, project, user, is_public=True)

        await svc.set_post_meta(db_session, user, project, page.id, description="opis")

        await db_session.refresh(page)
        assert page.is_public is True

    async def test_partial_write_keeps_fields_not_passed(self, db_session):
        user, project = await _setup(db_session, "beh-partial")
        page = await _make_page(db_session, project, user)
        published = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
        verified = date(2026, 4, 1)
        await _make_post(
            db_session,
            page,
            description="stary opis",
            author_name="Jan Kowalski",
            tags=["alfa", "beta"],
            lang="en",
            published_at=published,
            last_verified_at=verified,
        )

        post = await svc.set_post_meta(db_session, user, project, page.id, description="nowy opis")

        assert post.description == "nowy opis"
        assert post.author_name == "Jan Kowalski"
        assert post.tags == ["alfa", "beta"]
        assert post.lang == "en"
        assert post.published_at == published
        assert post.last_verified_at == verified
        assert post.slug == page.slug

    async def test_sets_published_at_and_last_verified_at(self, db_session):
        user, project = await _setup(db_session, "beh-dates")
        page = await _make_page(db_session, project, user)
        published = datetime(2026, 5, 5, 8, 30, tzinfo=UTC)

        post = await svc.set_post_meta(db_session, user, project, page.id, published_at=published, last_verified_at=date(2026, 6, 6))

        assert post.published_at == published
        assert post.last_verified_at == date(2026, 6, 6)

    async def test_published_at_none_clears_date(self, db_session):
        user, project = await _setup(db_session, "beh-dates-clear")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page, published_at=datetime(2026, 5, 5, tzinfo=UTC))

        post = await svc.set_post_meta(db_session, user, project, page.id, published_at=None)

        assert post.published_at is None

    async def test_slug_change_appends_old_slug_to_previous_slugs(self, db_session):
        user, project = await _setup(db_session, "beh-slug")
        page = await _make_page(db_session, project, user)
        old = page.slug
        new = f"nowy-{_uid()}"

        post = await svc.set_post_meta(db_session, user, project, page.id, slug=new)

        assert post.slug == new
        assert post.previous_slugs == [old]

    async def test_two_slug_changes_keep_both_old_slugs_in_order(self, db_session):
        user, project = await _setup(db_session, "beh-slug2")
        page = await _make_page(db_session, project, user)
        first = page.slug
        second = f"drugi-{_uid()}"
        third = f"trzeci-{_uid()}"

        await svc.set_post_meta(db_session, user, project, page.id, slug=second)
        post = await svc.set_post_meta(db_session, user, project, page.id, slug=third)

        assert post.slug == third
        assert post.previous_slugs == [first, second]

    async def test_returning_to_old_slug_removes_it_from_previous_slugs(self, db_session):
        user, project = await _setup(db_session, "beh-slug-back")
        page = await _make_page(db_session, project, user)
        original = page.slug
        await svc.set_post_meta(db_session, user, project, page.id, slug=f"tymczasowy-{_uid()}")

        post = await svc.set_post_meta(db_session, user, project, page.id, slug=original)

        assert post.slug == original
        assert original not in post.previous_slugs

    async def test_same_slug_leaves_previous_slugs_untouched(self, db_session):
        user, project = await _setup(db_session, "beh-slug-same")
        page = await _make_page(db_session, project, user)

        post = await svc.set_post_meta(db_session, user, project, page.id, slug=page.slug)

        assert post.slug == page.slug
        assert post.previous_slugs == []

    async def test_slug_taken_by_other_post_rejected_and_unchanged(self, db_session):
        user, project = await _setup(db_session, "beh-slug-taken")
        taken_page = await _make_page(db_session, project, user)
        await _make_post(db_session, taken_page)
        page = await _make_page(db_session, project, user)
        original = page.slug

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, slug=taken_page.slug)

        post = await _post_row(db_session, page.id)
        assert post is None or post.slug == original

    async def test_slug_in_other_posts_previous_slugs_rejected(self, db_session):
        user, project = await _setup(db_session, "beh-slug-prevtaken")
        old = f"zwolniony-{_uid()}"
        holder_page = await _make_page(db_session, project, user)
        await _make_post(db_session, holder_page, previous_slugs=[old])
        page = await _make_page(db_session, project, user)

        with pytest.raises(ValueError):
            await svc.set_post_meta(db_session, user, project, page.id, slug=old)

    async def test_commits(self, db_session):
        """db-commit.md: spy na commit pada na mutancie 'commit -> flush'."""
        user, project = await _setup(db_session, "beh-commit")
        page = await _make_page(db_session, project, user)
        spy = AsyncMock(side_effect=db_session.commit)

        with patch.object(db_session, "commit", spy):
            await svc.set_post_meta(db_session, user, project, page.id, description="opis")

        spy.assert_awaited_once()


class TestSetPostMetaPersistence:
    async def test_write_is_visible_in_new_session(self, engine):
        """Kryterium 0318ef79: zapis przeżywa zamknięcie sesji (commit, nie sam flush)."""
        factory = async_sessionmaker(engine, expire_on_commit=False)
        tag = _uid()
        slug = f"persist-blog-{tag}"

        async with factory() as setup_db:
            user = User(email=f"blogsvc-persist-{tag}@example.com", is_superuser=False)
            setup_db.add(user)
            await setup_db.flush()
            project = Project(
                name=f"Persist {tag}",
                slug=f"persist-{tag}",
                code="P" + tag[:5].upper(),
                api_key=secrets.token_urlsafe(32),
                is_active=True,
            )
            setup_db.add(project)
            await setup_db.flush()
            setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
            page = WikiPage(
                project_id=project.id,
                title="Persist",
                slug=slug,
                position=0,
                minio_path=f"{project.slug}/{tag}.md",
                is_public=True,
                created_by_id=user.id,
                last_edited_by_id=user.id,
            )
            setup_db.add(page)
            await setup_db.commit()
            user_id, project_id, page_id = user.id, project.id, page.id

        try:
            async with factory() as write_db:
                writer = await write_db.get(User, user_id)
                project_row = await write_db.get(Project, project_id)
                await svc.set_post_meta(write_db, writer, project_row, page_id, description="trwały opis", tags=["trwale"])

            async with factory() as read_db:
                saved = (await read_db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one_or_none()
            assert saved is not None
            assert saved.description == "trwały opis"
            assert saved.tags == ["trwale"]
        finally:
            async with factory() as cleanup_db:
                await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
                await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
                await cleanup_db.execute(delete(Project).where(Project.id == project_id))
                await cleanup_db.execute(delete(User).where(User.id == user_id))
                await cleanup_db.commit()


class TestGetPublicPostBySlug:
    async def test_current_slug_returns_post_and_page(self, db_session):
        user, project = await _setup(db_session, "get-cur")
        page = await _make_page(db_session, project, user, is_public=True)
        post = await _make_post(db_session, page)

        result = await svc.get_public_post_by_slug(db_session, page.slug)

        assert result is not None
        assert result.post is not None
        assert result.post.page_id == post.page_id
        assert result.page is not None
        assert result.page.id == page.id
        assert result.redirect_slug is None

    async def test_old_slug_returns_redirect_to_current(self, db_session):
        user, project = await _setup(db_session, "get-old")
        page = await _make_page(db_session, project, user, is_public=True)
        old = f"stary-{_uid()}"
        new = f"nowy-{_uid()}"
        await _make_post(db_session, page, slug=new, previous_slugs=[old])

        result = await svc.get_public_post_by_slug(db_session, old)

        assert result is not None
        assert result.redirect_slug == new
        assert result.post is None
        assert result.page is None

    async def test_private_post_returns_none_for_current_slug(self, db_session):
        user, project = await _setup(db_session, "get-priv")
        page = await _make_page(db_session, project, user, is_public=False)
        await _make_post(db_session, page)

        assert await svc.get_public_post_by_slug(db_session, page.slug) is None

    async def test_private_post_returns_none_for_old_slug(self, db_session):
        user, project = await _setup(db_session, "get-priv-old")
        page = await _make_page(db_session, project, user, is_public=False)
        old = f"stary-{_uid()}"
        await _make_post(db_session, page, slug=f"nowy-{_uid()}", previous_slugs=[old])

        assert await svc.get_public_post_by_slug(db_session, old) is None

    async def test_unknown_slug_returns_none(self, db_session):
        assert await svc.get_public_post_by_slug(db_session, f"nie-ma-{_uid()}") is None

    async def test_public_page_without_post_returns_none(self, db_session):
        """Brak fallbacku po slugu wiki: gwarancję daje ensure_post + migracja."""
        user, project = await _setup(db_session, "get-nopost")
        page = await _make_page(db_session, project, user, is_public=True)

        assert await svc.get_public_post_by_slug(db_session, page.slug) is None


class TestListPublicPosts:
    async def _seed(self, db, project, user, slug_prefix: str, tag: str, *, published_at, is_public=True, lang="pl", extra_tags=()):
        page = await _make_page(db, project, user, slug=f"{slug_prefix}-{tag}", is_public=is_public)
        return await _make_post(db, page, published_at=published_at, lang=lang, tags=[tag, *extra_tags])

    async def test_sorted_by_published_at_descending_nulls_last(self, db_session):
        user, project = await _setup(db_session, "list-sort")
        tag = f"s{_uid()}"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        # Kolejność wstawiania celowo różna od oczekiwanej (nowy, brak daty, najstarszy, środkowy).
        newest = await self._seed(db_session, project, user, "a", tag, published_at=base + timedelta(days=10))
        undated = await self._seed(db_session, project, user, "b", tag, published_at=None)
        oldest = await self._seed(db_session, project, user, "c", tag, published_at=base)
        middle = await self._seed(db_session, project, user, "d", tag, published_at=base + timedelta(days=5))

        result = await svc.list_public_posts(db_session, 1, 20, tag=tag)

        assert [p.post.slug for p in result.items] == [newest.slug, middle.slug, oldest.slug, undated.slug]

    async def test_equal_published_at_ordered_by_slug_ascending(self, db_session):
        user, project = await _setup(db_session, "list-tie")
        tag = f"t{_uid()}"
        same = datetime(2026, 2, 2, tzinfo=UTC)
        zzz = await self._seed(db_session, project, user, "zzz", tag, published_at=same)
        aaa = await self._seed(db_session, project, user, "aaa", tag, published_at=same)
        mmm = await self._seed(db_session, project, user, "mmm", tag, published_at=same)

        result = await svc.list_public_posts(db_session, 1, 20, tag=tag)

        assert [p.post.slug for p in result.items] == [aaa.slug, mmm.slug, zzz.slug]

    async def test_private_posts_are_skipped(self, db_session):
        user, project = await _setup(db_session, "list-priv")
        tag = f"p{_uid()}"
        pub = await self._seed(db_session, project, user, "pub", tag, published_at=datetime(2026, 1, 1, tzinfo=UTC))
        await self._seed(db_session, project, user, "priv", tag, published_at=datetime(2026, 1, 2, tzinfo=UTC), is_public=False)

        result = await svc.list_public_posts(db_session, 1, 20, tag=tag)

        assert [p.post.slug for p in result.items] == [pub.slug]
        assert result.total == 1

    async def test_tag_filter_matches_list_element_only(self, db_session):
        user, project = await _setup(db_session, "list-tag")
        tag = f"g{_uid()}"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        with_tag = await self._seed(db_session, project, user, "with", tag, published_at=base, extra_tags=["inny"])
        page = await _make_page(db_session, project, user, slug=f"other-{tag}", is_public=True)
        await _make_post(db_session, page, published_at=base + timedelta(days=1), tags=[f"{tag}-extra"])
        no_tags_page = await _make_page(db_session, project, user, slug=f"none-{tag}", is_public=True)
        await _make_post(db_session, no_tags_page, published_at=base + timedelta(days=2))

        result = await svc.list_public_posts(db_session, 1, 20, tag=tag)

        assert [p.post.slug for p in result.items] == [with_tag.slug]

    async def test_lang_filter(self, db_session):
        user, project = await _setup(db_session, "list-lang")
        tag = f"l{_uid()}"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        pl_post = await self._seed(db_session, project, user, "pl", tag, published_at=base, lang="pl")
        en_post = await self._seed(db_session, project, user, "en", tag, published_at=base + timedelta(days=1), lang="en")

        en_result = await svc.list_public_posts(db_session, 1, 20, tag=tag, lang="en")
        pl_result = await svc.list_public_posts(db_session, 1, 20, tag=tag, lang="pl")

        assert [p.post.slug for p in en_result.items] == [en_post.slug]
        assert [p.post.slug for p in pl_result.items] == [pl_post.slug]

    async def test_items_carry_matching_page(self, db_session):
        user, project = await _setup(db_session, "list-page")
        tag = f"m{_uid()}"
        post = await self._seed(db_session, project, user, "x", tag, published_at=datetime(2026, 1, 1, tzinfo=UTC))

        result = await svc.list_public_posts(db_session, 1, 20, tag=tag)

        assert result.items[0].page.id == post.page_id

    async def test_pagination_splits_and_reports_totals(self, db_session):
        user, project = await _setup(db_session, "list-pages")
        tag = f"n{_uid()}"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        slugs_by_age = []
        # Wstawiane od najstarszego, wynik ma być od najnowszego.
        for i in range(5):
            post = await self._seed(db_session, project, user, f"p{i}", tag, published_at=base + timedelta(days=i))
            slugs_by_age.append(post.slug)
        newest_first = list(reversed(slugs_by_age))

        first = await svc.list_public_posts(db_session, 1, 2, tag=tag)
        last = await svc.list_public_posts(db_session, 3, 2, tag=tag)

        assert [p.post.slug for p in first.items] == newest_first[:2]
        assert [p.post.slug for p in last.items] == newest_first[4:]
        assert (first.total, first.per_page, first.total_pages, first.page) == (5, 2, 3, 1)

    async def test_page_beyond_range_is_clamped_to_last_page(self, db_session):
        user, project = await _setup(db_session, "list-clamp-hi")
        tag = f"c{_uid()}"
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(3):
            await self._seed(db_session, project, user, f"q{i}", tag, published_at=base + timedelta(days=i))

        result = await svc.list_public_posts(db_session, 99, 2, tag=tag)

        assert result.page == 2
        assert len(result.items) == 1

    async def test_page_below_one_is_clamped_to_first_page(self, db_session):
        user, project = await _setup(db_session, "list-clamp-lo")
        tag = f"d{_uid()}"
        await self._seed(db_session, project, user, "r", tag, published_at=datetime(2026, 1, 1, tzinfo=UTC))

        result = await svc.list_public_posts(db_session, 0, 2, tag=tag)

        assert result.page == 1
        assert len(result.items) == 1

    async def test_empty_result_has_one_total_page(self, db_session):
        result = await svc.list_public_posts(db_session, 5, 20, tag=f"brak-{_uid()}")

        assert result.items == []
        assert result.total == 0
        assert result.total_pages == 1
        assert result.page == 1
