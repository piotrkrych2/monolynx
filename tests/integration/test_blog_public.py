"""Testy integracyjne publicznego bloga -- GET /blog/, GET /blog/{slug} (MON-201, MON-202, MON-203, MON-254, MON-260).

MON-260 (szablon wpisu i listy dla ludzi): spis treści, meta wpisu, tagi, okładka, powiązane,
tłumaczenia z `hreflang`, filtry `?tag=` / `?lang=` z paginacją, `ETag` / `304`, język interfejsu.
Asercje HTML celują w klasy-haki (`blog-toc`, `blog-related`, ...), nie w klasy Tailwind.

WAZNE: endpointy /blog/* dzialaja BEZ sesji i BEZ project_slug w URL. Testy tras nie
uzywaja login_session -- goly `client` bez logowania potwierdza dostep anonimowy. Wyjatek:
testy wejscia dashboardu (TestPublishCreatesBlogPost) loguja sie, bo sprawdzaja zapis
przez formularz.

MON-254: adres wpisu to stabilny `BlogPost.slug` (nie slug strony wiki), lista sortuje po
`published_at`, a strona publiczna bez wiersza BlogPost daje 404. Helper `_create_wiki_page`
tworzy wiec dla stron publicznych tez BlogPost.

MON-257: `TestBlogGraphicBlocks` sprawdza bloki graficzne na realnej trasie: biblioteka Mermaid
tylko we wpisie z diagramem (raz), skrypt i styl komponentu z `BLOG_GRAPHICS` tylko we wpisie,
ktory go uzywa (raz), bliźniak `.md` bez HTML i pliki statyczne komponentu `agent-loop`.
"""

import html
import re
import secrets
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from monolynx import mcp_server
from monolynx.config import settings
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services import blog as blog_service
from monolynx.services.auth import hash_password
from monolynx.services.blog_blocks import render_post_markdown as real_render_post_markdown
from monolynx.services.mcp_auth import generate_api_token
from monolynx.services.wiki import create_wiki_page, update_wiki_page
from tests.conftest import login_session
from tests.integration.api_v2.conftest import make_owner_project

# ---------------------------------------------------------------------------
# Helpers lokalne (konwencja z tests/integration/test_wiki_dashboard.py,
# ale bez importu login_session -- endpoint publiczny nie wymaga sesji)
# ---------------------------------------------------------------------------


_NOW: object = object()  # sentinel: published_at "teraz" (None oznacza wpis bez daty publikacji)


async def _create_project(db_session, slug, code=None):
    """Helper: tworzy projekt z unikalnym kodem."""
    if code is None:
        code = "B" + secrets.token_hex(4).upper()
    project = Project(
        name=f"Blog {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user(db_session, email):
    """Helper: tworzy uzytkownika w DB (bez logowania -- endpoint publiczny go nie wymaga).

    Konto ma `can_edit_blog=True` (MON-270): helper sluzy do zakladania autorow wpisow, a zapisy
    publikacji i `is_public` przez serwis wymagaja uprawnienia `blog:write`."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=False,
        can_edit_blog=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _create_attachment(db_session, page, filename="report.pdf", mime_type="application/pdf", storage_path=None, size=100):
    """Helper: tworzy zalacznik strony wiki bezposrednio w DB (bez realnego MinIO)."""
    att = WikiAttachment(
        wiki_page_id=page.id,
        filename=filename,
        storage_path=storage_path or f"wiki-attachments/{page.id}/{filename}",
        mime_type=mime_type,
        size=size,
        created_via_ai=False,
    )
    db_session.add(att)
    await db_session.flush()
    return att


async def _create_wiki_page(
    db_session,
    project,
    user,
    title,
    minio_path=None,
    parent_id=None,
    is_public=False,
    updated_at=None,
    *,
    with_post=None,
    post_slug=None,
    published_at=_NOW,
    created_at=None,
    **post_fields,
):
    """Helper: tworzy strone wiki bezposrednio w DB (bez realnego MinIO) i jej BlogPost.

    updated_at: jawne przypisanie PRZED pierwszym flush (INSERT) nadpisuje
    server_default -- potrzebne do deterministycznego sortowania w testach listy
    /blog/ (wzorzec MON-87, patrz .claude/agent-memory/qa-tester/MEMORY.md).
    with_post: domyslnie BlogPost powstaje dla stron publicznych; True wymusza go tez dla
    prywatnej (wpis cofniety z publikacji). post_slug / published_at / post_fields
    (description, tags, cover_attachment_id, previous_slugs, ...) trafiaja do BlogPost;
    published_at domyslnie "teraz", a jawne None daje wpis bez daty publikacji (fallback na
    created_at strony). created_at nadpisuje czas utworzenia strony PRZED pierwszym flush.
    """
    page = WikiPage(
        project_id=project.id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        position=0,
        minio_path=minio_path or f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
        parent_id=parent_id,
    )
    if updated_at is not None:
        page.updated_at = updated_at
    if created_at is not None:
        page.created_at = created_at
    db_session.add(page)
    await db_session.flush()
    if with_post if with_post is not None else is_public:
        db_session.add(
            BlogPost(
                page_id=page.id,
                slug=post_slug or page.slug,
                published_at=datetime.now(UTC) if published_at is _NOW else published_at,
                **post_fields,
            )
        )
        await db_session.flush()
    return page


# ---------------------------------------------------------------------------
# 1. Dostep anonimowy -- 404 dla prywatnych/nieistniejacych, 200 dla publicznych
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogPublicAccess:
    """GET /blog/{slug} bez sesji: is_public=False -> 404, is_public=True -> 200."""

    async def test_private_page_returns_404_without_session(self, client, db_session):
        """Strona is_public=False -> 404 dla zadania anonimowego (kryterium ad92bbc0).

        Jawnie NIE 403 -- 403 potwierdzalby istnienie zasobu.
        """
        project = await _create_project(db_session, "blog-priv")
        user = await _create_user(db_session, "blog-priv@test.com")
        page = await _create_wiki_page(db_session, project, user, "Strona Prywatna MON201", is_public=False)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 404
        assert resp.status_code != 403

    async def test_nonexistent_slug_returns_404(self, client, db_session):
        """Nieistniejacy slug -> 404."""
        resp = await client.get("/blog/nie-ma-takiej-strony-mon201")

        assert resp.status_code == 404
        assert resp.status_code != 403

    @patch("monolynx.services.wiki.get_markdown")
    async def test_public_page_returns_200_with_minio_content(self, mock_get_markdown, client, db_session):
        """Strona is_public=True -> 200, tresc z (zmockowanego) MinIO widoczna w HTML (kryterium 9149e756)."""
        mock_get_markdown.return_value = "Tresc markera TRESC-Z-MINIO-MON201-XYZ."

        project = await _create_project(db_session, "blog-pub")
        user = await _create_user(db_session, "blog-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, "Strona Publiczna MON201", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "TRESC-Z-MINIO-MON201-XYZ" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_xss_payload_in_public_page_is_not_in_response(self, mock_get_markdown, client, db_session):
        """Ladunek XSS w tresci strony publicznej nie trafia do odpowiedzi HTTP (kryterium 0c0d0be9)."""
        mock_get_markdown.return_value = (
            'Artykul.\n\n<script>alert(\'mon201-xss\')</script>\n\n<img src="x" onerror="alert(1)">\n\n[Link](javascript:alert(2))'
        )

        project = await _create_project(db_session, "blog-xss")
        user = await _create_user(db_session, "blog-xss@test.com")
        page = await _create_wiki_page(db_session, project, user, "Strona Z XSS MON201", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        # Celujemy w sam ladunek (nie w dowolny <script> - layout blog.html dziedziczy
        # z layouts/landing.html, ktory legalnie zawiera wlasne <script> - Tailwind CDN,
        # przelacznik motywu, Google Analytics). "mon201-xss" wystapiloby tylko gdyby
        # wstrzykniety <script>alert('mon201-xss')</script> z markdownu przeszedl sanityzacje.
        assert "mon201-xss" not in resp.text
        assert "onerror=" not in resp.text
        assert "javascript:" not in resp.text


# ---------------------------------------------------------------------------
# 2. Brak wycieku prywatnej strony (kryterium 0c82255d)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogPublicNoLeak:
    """Zadanie bez sesji nigdy nie ujawnia tresci strony is_public=False."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_private_page_content_never_appears_in_response(self, mock_get_markdown, client, db_session):
        """Ani tytul, ani slug, ani tresc prywatnej strony nie pojawiaja sie w odpowiedzi (kryterium 0c82255d)."""
        mock_get_markdown.return_value = "TAJNA-TRESC-KTORA-NIE-MOZE-WYCIEKNAC-MON201"

        project = await _create_project(db_session, "blog-noleak")
        user = await _create_user(db_session, "blog-noleak@test.com")
        page = await _create_wiki_page(
            db_session,
            project,
            user,
            "Sekretny Tytul MON201 Noleak",
            is_public=False,
        )

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 404
        assert "Sekretny Tytul MON201 Noleak" not in resp.text
        assert page.slug not in resp.text
        assert "TAJNA-TRESC-KTORA-NIE-MOZE-WYCIEKNAC-MON201" not in resp.text

    async def test_private_page_not_reachable_via_different_project_same_case(self, client, db_session):
        """Prywatna strona pozostaje niedostepna niezaleznie od tego, w ktorym projekcie zyje."""
        project_a = await _create_project(db_session, "blog-noleak-a")
        project_b = await _create_project(db_session, "blog-noleak-b")
        user = await _create_user(db_session, "blog-noleak-2@test.com")
        await _create_wiki_page(db_session, project_a, user, "Prywatna A", is_public=False)
        page_b_private = await _create_wiki_page(db_session, project_b, user, "Prywatna B", is_public=False)

        resp = await client.get(f"/blog/{page_b_private.slug}")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Hierarchia: breadcrumbs / dzieci / backlinki nie ujawniaja prywatnych (kryterium 86569fcb)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogPublicHierarchyFiltering:
    """Publiczna strona z prywatnym rodzicem i prywatnym dzieckiem -- ich tytuly/slugi
    nie moga pojawic sie w odpowiedzi. Publiczne dziecko MA byc widoczne (kontrast)."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_private_parent_and_child_not_leaked_public_child_is_visible(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc strony publicznej."

        project = await _create_project(db_session, "blog-hier")
        user = await _create_user(db_session, "blog-hier@test.com")

        private_root = await _create_wiki_page(db_session, project, user, "Prywatny Korzen MON201", is_public=False)
        public_page = await _create_wiki_page(
            db_session,
            project,
            user,
            "Publiczna Strona Hierarchia MON201",
            parent_id=private_root.id,
            is_public=True,
        )
        private_child = await _create_wiki_page(
            db_session,
            project,
            user,
            "Prywatne Dziecko MON201",
            parent_id=public_page.id,
            is_public=False,
        )
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Publiczne Dziecko MON201",
            parent_id=public_page.id,
            is_public=True,
        )

        resp = await client.get(f"/blog/{public_page.slug}")

        assert resp.status_code == 200
        # Prywatny rodzic -- tytul i slug nie moga wyciekac (breadcrumbs)
        assert "Prywatny Korzen MON201" not in resp.text
        assert private_root.slug not in resp.text
        # Prywatne dziecko -- tytul i slug nie moga wyciekac (lista dzieci)
        assert "Prywatne Dziecko MON201" not in resp.text
        assert private_child.slug not in resp.text
        # Kontrast: publiczne dziecko MA byc widoczne
        assert "Publiczne Dziecko MON201" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_private_backlink_source_not_leaked(self, mock_get_markdown, client, db_session):
        """Backlink, ktorego zrodlem jest prywatna strona, nie ujawnia tej strony na
        publicznej stronie docelowej (backlinki filtrowane po is_public=True)."""
        mock_get_markdown.return_value = "Tresc strony publicznej z backlinkami."

        project = await _create_project(db_session, "blog-backlink")
        user = await _create_user(db_session, "blog-backlink@test.com")

        public_page = await _create_wiki_page(db_session, project, user, "Publiczna Z Backlinkami MON201", is_public=True)
        private_linker = await _create_wiki_page(db_session, project, user, "Sekretny Linker MON201", is_public=False)
        public_linker = await _create_wiki_page(db_session, project, user, "Publiczny Linker MON201", is_public=True)

        db_session.add(
            WikiBacklink(
                source_page_id=private_linker.id,
                target_page_id=public_page.id,
                anchor_text="Sekretny Linker MON201",
            )
        )
        db_session.add(
            WikiBacklink(
                source_page_id=public_linker.id,
                target_page_id=public_page.id,
                anchor_text="Publiczny Linker MON201",
            )
        )
        await db_session.flush()

        resp = await client.get(f"/blog/{public_page.slug}")

        assert resp.status_code == 200
        assert "Sekretny Linker MON201" not in resp.text
        assert private_linker.slug not in resp.text
        # Kontrast: backlink z publicznej strony MA byc widoczny
        assert "Publiczny Linker MON201" in resp.text


# ---------------------------------------------------------------------------
# 4. Konflikt slugu publicznego miedzy projektami (kryterium 752ae28e)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPublicSlugConflict:
    """Dwie strony is_public=True z roznych projektow o tym samym slugu -- druga proba
    konczy sie czytelnym bledem (nie 500 z IntegrityError). Test na poziomie serwisu:
    `uq_wiki_public_slug` jest partial unique index globalny (bez project_id), wiec
    per-projektowe dedupowanie slugow w create_wiki_page (_ensure_unique_slug) go NIE lapie.
    """

    async def test_create_second_public_page_same_slug_different_project_raises_readable_error(self, db_session):
        project_a = await _create_project(db_session, "blog-conflict-a")
        project_b = await _create_project(db_session, "blog-conflict-b")
        user = await _create_user(db_session, "blog-conflict@test.com")

        with (
            patch("monolynx.services.wiki.upload_markdown", return_value="blog-conflict-a/x.md"),
            patch("monolynx.services.embeddings.update_page_embeddings"),
            patch("monolynx.services.wiki.sync_backlinks"),
        ):
            await create_wiki_page(
                project_id=project_a.id,
                project_slug=project_a.slug,
                title="Architektura Konflikt MON201",
                content="# A",
                user_id=user.id,
                is_public=True,
                db=db_session,
            )

        with (
            patch("monolynx.services.wiki.upload_markdown", return_value="blog-conflict-b/y.md"),
            patch("monolynx.services.embeddings.update_page_embeddings"),
            patch("monolynx.services.wiki.sync_backlinks"),
            pytest.raises(ValueError),
        ):
            await create_wiki_page(
                project_id=project_b.id,
                project_slug=project_b.slug,
                title="Architektura Konflikt MON201",
                content="# B",
                user_id=user.id,
                is_public=True,
                db=db_session,
            )

    async def test_marking_existing_private_page_public_with_conflicting_slug_raises_readable_error(self, db_session):
        """Prywatna strona ma taki sam slug jak istniejaca publiczna strona z innego
        projektu (dozwolone -- slug jest unikalny tylko per-projekt). Oznaczenie jej
        jako is_public=True musi konczyc sie czytelnym bledem, nie 500."""
        project_a = await _create_project(db_session, "blog-conflict-c")
        project_b = await _create_project(db_session, "blog-conflict-d")
        user = await _create_user(db_session, "blog-conflict-2@test.com")

        with (
            patch("monolynx.services.wiki.upload_markdown", return_value="blog-conflict-c/x.md"),
            patch("monolynx.services.embeddings.update_page_embeddings"),
            patch("monolynx.services.wiki.sync_backlinks"),
        ):
            await create_wiki_page(
                project_id=project_a.id,
                project_slug=project_a.slug,
                title="Duplikat Slug MON201",
                content="# A",
                user_id=user.id,
                is_public=True,
                db=db_session,
            )

        private_page = await _create_wiki_page(db_session, project_b, user, "Duplikat Slug MON201", is_public=False)

        with pytest.raises(ValueError):
            await update_wiki_page(
                page=private_page,
                project_slug=project_b.slug,
                is_public=True,
                user_id=user.id,
                db=db_session,
            )

    async def test_create_public_page_with_slug_of_unpublished_post_raises_and_rolls_back(self, db_session):
        """Slug nalezy do wpisu strony cofnietej z publikacji (BlogPost zostaje, strona prywatna).
        create_wiki_page(is_public=True) w innym projekcie rzuca ValueError z ensure_post (nie
        IntegrityError), a rollback usuwa strone, ktora autoflush zdazyl wstawic."""
        project_a = await _create_project(db_session, "blog-conflict-e")
        project_b = await _create_project(db_session, "blog-conflict-f")
        user = await _create_user(db_session, "blog-conflict-3@test.com")
        title = "Slug Cofniety Create"
        await _create_wiki_page(db_session, project_a, user, title, is_public=False, with_post=True)
        project_b_id, project_b_slug, user_id = project_b.id, project_b.slug, user.id

        with _wiki_side_effects(), pytest.raises(ValueError, match="zajęty przez inny wpis"):
            await create_wiki_page(
                project_id=project_b_id,
                project_slug=project_b_slug,
                title=title,
                content="# B",
                user_id=user_id,
                is_public=True,
                db=db_session,
            )

        leftover = (await db_session.execute(select(WikiPage.id).where(WikiPage.project_id == project_b_id))).all()
        assert leftover == []

    async def test_update_page_to_public_with_slug_of_unpublished_post_raises_and_rolls_back(self, db_session):
        """Jak wyzej dla update_wiki_page: ValueError z ensure_post, po nim sesja jest uzywalna,
        a strona nie zostaje publiczna (rollback cofa UPDATE wstawiony przez autoflush)."""
        project_a = await _create_project(db_session, "blog-conflict-g")
        project_b = await _create_project(db_session, "blog-conflict-h")
        user = await _create_user(db_session, "blog-conflict-4@test.com")
        title = "Slug Cofniety Update"
        await _create_wiki_page(db_session, project_a, user, title, is_public=False, with_post=True)
        private_page = await _create_wiki_page(db_session, project_b, user, title, is_public=False)
        page_id, project_b_slug, user_id = private_page.id, project_b.slug, user.id

        with _wiki_side_effects(), pytest.raises(ValueError, match="zajęty przez inny wpis"):
            await update_wiki_page(page=private_page, project_slug=project_b_slug, is_public=True, user_id=user_id, db=db_session)

        public_rows = (await db_session.execute(select(WikiPage.id).where(WikiPage.id == page_id, WikiPage.is_public.is_(True)))).all()
        assert public_rows == []


# ---------------------------------------------------------------------------
# 5. Publiczne zalaczniki wpisu -- GET /blog/{slug}/attachments/{attachment_id}/{filename}
# (MON-203, kryteria 4, 5, 6)
#
# blog.py importuje get_attachment z services/minio_client.py pod aliasem
# "minio_get_attachment" (jak dashboard/wiki.py) -- stad patch celuje w
# "monolynx.blog.minio_get_attachment". Od MON-254 trasa szuka wpisu po aktualnym
# BlogPost.slug (stary slug -> 404 bez 301, patrz TestBlogSlugRedirect).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogPublicAttachmentServe:
    """Kryteria 4, 5, 6 (MON-203)."""

    @patch("monolynx.blog.minio_get_attachment", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    async def test_public_page_attachment_served_without_session(self, mock_get_att, client, db_session):
        """Kryterium 4: zalacznik strony publicznej pobiera sie BEZ SESJI (goly `client`,
        bez login_session -- konsekwentnie z reszta tego pliku)."""
        project = await _create_project(db_session, "blog-att-pub")
        user = await _create_user(db_session, "blog-att-pub@test.com")
        page = await _create_wiki_page(db_session, project, user, "Publiczna Z Obrazkiem MON203", is_public=True)
        att = await _create_attachment(db_session, page, filename="cover.png", mime_type="image/png")

        resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 200
        assert resp.content == b"\x89PNG\r\n\x1a\n"
        assert resp.headers["content-type"] == "image/png"
        mock_get_att.assert_called_once_with(att.storage_path)

    async def test_private_page_attachment_returns_404_without_session(self, client, db_session):
        """Kryterium 4: zalacznik strony prywatnej -> 404 pod tym samym adresem."""
        project = await _create_project(db_session, "blog-att-priv")
        user = await _create_user(db_session, "blog-att-priv@test.com")
        page = await _create_wiki_page(db_session, project, user, "Prywatna Z Obrazkiem MON203", is_public=False)
        att = await _create_attachment(db_session, page, filename="secret.png", mime_type="image/png")

        resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 404

    async def test_nonexistent_page_slug_attachment_returns_404(self, client, db_session):
        fake_att_id = uuid.uuid4()
        resp = await client.get(f"/blog/nie-ma-takiej-strony-mon203-att/attachments/{fake_att_id}/file.png")
        assert resp.status_code == 404

    async def test_nonexistent_attachment_id_returns_404(self, client, db_session):
        project = await _create_project(db_session, "blog-att-noatt")
        user = await _create_user(db_session, "blog-att-noatt@test.com")
        page = await _create_wiki_page(db_session, project, user, "Publiczna Bez Zalacznika MON203", is_public=True)
        fake_att_id = uuid.uuid4()

        resp = await client.get(f"/blog/{page.slug}/attachments/{fake_att_id}/file.png")

        assert resp.status_code == 404

    async def test_attachment_belonging_to_different_page_returns_404(self, client, db_session):
        """Kryterium 6: attachment_id istnieje i obie strony sa publiczne, ale zalacznik
        nalezy do INNEJ strony niz {slug} w URL -> 404."""
        project = await _create_project(db_session, "blog-att-crosspage")
        user = await _create_user(db_session, "blog-att-crosspage@test.com")
        page_a = await _create_wiki_page(db_session, project, user, "Publiczna A MON203", is_public=True)
        page_b = await _create_wiki_page(db_session, project, user, "Publiczna B MON203", is_public=True)
        att_of_a = await _create_attachment(db_session, page_a, filename="only-on-a.png", mime_type="image/png")

        resp = await client.get(f"/blog/{page_b.slug}/attachments/{att_of_a.id}/{att_of_a.filename}")

        assert resp.status_code == 404

    @pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/gif", "image/webp"])
    async def test_allowlisted_image_mime_served_inline(self, mime_type, client, db_session):
        """Kryterium 5: typy z allow-listy -> Content-Disposition: inline."""
        variant = mime_type.split("/")[1]
        project = await _create_project(db_session, f"blog-att-inline-{variant}")
        user = await _create_user(db_session, f"blog-att-inline-{variant}@test.com")
        page = await _create_wiki_page(db_session, project, user, f"Inline {variant} MON203", is_public=True)
        att = await _create_attachment(db_session, page, filename=f"pic.{variant}", mime_type=mime_type)

        with patch("monolynx.blog.minio_get_attachment", return_value=(b"binary-data", mime_type)):
            resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 200
        assert resp.headers.get("content-disposition", "").startswith("inline")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_non_image_mime_served_as_attachment_with_nosniff(self, client, db_session):
        """Kryterium 5: typ inny niz obrazkowy (np. PDF) -> Content-Disposition: attachment
        + naglowek X-Content-Type-Options: nosniff."""
        project = await _create_project(db_session, "blog-att-pdf")
        user = await _create_user(db_session, "blog-att-pdf@test.com")
        page = await _create_wiki_page(db_session, project, user, "Publiczna Z PDF MON203", is_public=True)
        att = await _create_attachment(db_session, page, filename="doc.pdf", mime_type="application/pdf")

        with patch("monolynx.blog.minio_get_attachment", return_value=(b"%PDF-1.4", "application/pdf")):
            resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 200
        assert resp.headers.get("content-disposition", "").startswith("attachment")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_svg_mime_served_as_attachment_not_inline(self, client, db_session):
        """Kryterium 5: image/svg+xml NIE jest na allow-liscie (SVG moze wykonac skrypt) ->
        musi isc jako attachment, nigdy inline, mimo ze zaczyna sie od 'image/'."""
        project = await _create_project(db_session, "blog-att-svg")
        user = await _create_user(db_session, "blog-att-svg@test.com")
        page = await _create_wiki_page(db_session, project, user, "Publiczna Z SVG MON203", is_public=True)
        att = await _create_attachment(db_session, page, filename="icon.svg", mime_type="image/svg+xml")

        with patch(
            "monolynx.blog.minio_get_attachment",
            return_value=(b"<svg onload='alert(1)'></svg>", "image/svg+xml"),
        ):
            resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 200
        assert resp.headers.get("content-disposition", "").startswith("attachment")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_private_page_attachment_response_still_has_nosniff_header(self, client, db_session):
        """Kryterium 5 (naglowek na KAZDEJ odpowiedzi tego endpointu) -- rowniez na 404,
        nie tylko na sukcesie."""
        project = await _create_project(db_session, "blog-att-nosniff404")
        user = await _create_user(db_session, "blog-att-nosniff404@test.com")
        page = await _create_wiki_page(db_session, project, user, "Prywatna Nosniff MON203", is_public=False)
        att = await _create_attachment(db_session, page, filename="secret.png", mime_type="image/png")

        resp = await client.get(f"/blog/{page.slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 404
        assert resp.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# MON-202: GET /blog/ -- lista publicznych wpisow z paginacja (kryterium 1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogIndexList:
    """GET /blog/ -- lista wylacznie stron is_public=True (kryterium 1 i 5)."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_index_lists_only_public_pages(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc do streszczenia MON202."

        project = await _create_project(db_session, "blog-idx-pub")
        user = await _create_user(db_session, "blog-idx-pub@test.com")
        await _create_wiki_page(db_session, project, user, "Publiczny Wpis Lista MON202", is_public=True)
        await _create_wiki_page(db_session, project, user, "Prywatny Wpis Lista MON202", is_public=False)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Publiczny Wpis Lista MON202" in resp.text
        assert "Prywatny Wpis Lista MON202" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_index_shows_summary_extracted_from_content(self, mock_get_markdown, client, db_session):
        """Streszczenie w liscie pochodzi z _extract_summary() tresci strony (pierwsza
        niepusta linia niebedaca naglowkiem markdown), nie z surowego markdownu 1:1."""
        mock_get_markdown.return_value = "# Naglowek ktory ma byc pominiety w streszczeniu\n\nStreszczenie widoczne w liscie MON202."

        project = await _create_project(db_session, "blog-idx-summary")
        user = await _create_user(db_session, "blog-idx-summary@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Ze Streszczeniem MON202", is_public=True)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Streszczenie widoczne w liscie MON202" in resp.text
        assert "Naglowek ktory ma byc pominiety w streszczeniu" not in resp.text

    async def test_index_returns_200_with_no_public_pages(self, client, db_session):
        """Pusta lista (brak publicznych stron w bazie) -- 200, nie 404/500."""
        resp = await client.get("/blog/")

        assert resp.status_code == 200


@pytest.mark.integration
class TestBlogIndexPagination:
    """GET /blog/ -- paginacja wzorcem projektu (kryterium 1): sortowanie po
    published_at malejaco, `page` z clampingiem do <1, total_pages>, bez 500 na zly input.

    Kolejnosc wstawiania (i=0..24) jest CELOWO odwrotna wzgledem oczekiwanej
    kolejnosci malejacej: i=0 ma NAJSTARSZY published_at, i=24 NAJNOWSZY. Gdyby
    implementacja nie sortowala wcale albo sortowala rosnaco, Postgres zwrocilby
    wiersze w kolejnosci fizycznej = kolejnosci wstawiania (00, 01, 02, ...) i
    strona 1 zawieralaby "00" zamiast "24" -- ponizsze asercje wtedy PADAJA.
    `updated_at` stron idzie w PRZECIWNA strone (i=0 najswiezszy), wiec sortowanie po
    updated_at (stare zachowanie sprzed MON-254) tez zmienia wynik.

    per_page jest ustalone w blog.py na 20 (wzorcem scrum.py) -- 25 seedowanych
    stron gwarantuje druga strone.
    """

    async def _seed_pages(self, db_session, project, user, mock_get_markdown):
        mock_get_markdown.return_value = "Tresc do streszczenia MON202 paginacja."
        base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        for i in range(25):
            await _create_wiki_page(
                db_session,
                project,
                user,
                f"Wpis Paginacja MON202 {i:02d}",
                is_public=True,
                updated_at=base - timedelta(minutes=i),
                published_at=base + timedelta(minutes=i),
            )

    @patch("monolynx.services.wiki.get_markdown")
    async def test_first_page_has_most_recently_published_not_oldest(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-page-1")
        user = await _create_user(db_session, "blog-page-1@test.com")
        await self._seed_pages(db_session, project, user, mock_get_markdown)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Wpis Paginacja MON202 24" in resp.text
        assert "Wpis Paginacja MON202 00" not in resp.text
        # Kolejnosc wzgledna w obrebie strony 1 (per_page=20 -> pozycje 24..05):
        # najnowszy wpis (24) musi byc wyrenderowany PRZED starszym (05).
        assert resp.text.index("Wpis Paginacja MON202 24") < resp.text.index("Wpis Paginacja MON202 05")

    @patch("monolynx.services.wiki.get_markdown")
    async def test_page_beyond_total_is_clamped_not_500(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-page-clamp")
        user = await _create_user(db_session, "blog-page-clamp@test.com")
        await self._seed_pages(db_session, project, user, mock_get_markdown)

        resp = await client.get("/blog/?page=999")

        assert resp.status_code == 200
        assert "Wpis Paginacja MON202 00" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_page_zero_falls_back_to_first_page(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-page-zero")
        user = await _create_user(db_session, "blog-page-zero@test.com")
        await self._seed_pages(db_session, project, user, mock_get_markdown)

        resp = await client.get("/blog/?page=0")

        assert resp.status_code == 200
        assert "Wpis Paginacja MON202 24" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_non_numeric_page_falls_back_to_first_page(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-page-nan")
        user = await _create_user(db_session, "blog-page-nan@test.com")
        await self._seed_pages(db_session, project, user, mock_get_markdown)

        resp = await client.get("/blog/?page=abc")

        assert resp.status_code == 200
        assert "Wpis Paginacja MON202 24" in resp.text


# ---------------------------------------------------------------------------
# MON-202: meta_description/og:description z tresci strony, og:type=article,
# article:published_time (kryterium 3) -- test end-to-end (prawdziwa baza + serwis
# _extract_summary(), w odroznieniu od tests/unit/test_blog.py, ktory renderuje sam
# szablon ze zmockowanym kontekstem).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlogPageMetaFromContent:
    """Kryterium 3: og:title/og:description/og:url wpisu biora sie z tresci strony,
    nie z wartosci domyslnych layoutu."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_meta_description_comes_from_extracted_summary(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "# Naglowek pomijany przez _extract_summary\n\nPierwsza-tresciwa-linia-MON202-QRS."

        project = await _create_project(db_session, "blog-meta-summary")
        user = await _create_user(db_session, "blog-meta-summary@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Summary MON202", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "Pierwsza-tresciwa-linia-MON202-QRS" in resp.text
        assert "wiki projektu Monolynx" not in resp.text
        assert "Monolynx project wiki" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_og_type_is_article_and_published_time_matches_published_at(self, mock_get_markdown, client, db_session):
        """article:published_time bierze sie z BlogPost.published_at (MON-254), nie z created_at strony."""
        mock_get_markdown.return_value = "Tresc artykulu MON202."

        project = await _create_project(db_session, "blog-meta-ogtype")
        user = await _create_user(db_session, "blog-meta-ogtype@test.com")
        published = datetime(2026, 1, 7, 6, 5, tzinfo=UTC)
        page = await _create_wiki_page(db_session, project, user, "Wpis Og Type MON202", is_public=True, published_at=published)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'property="og:type" content="article"' in resp.text
        assert f'property="article:published_time" content="{published.isoformat()}"' in resp.text
        assert page.created_at.isoformat() != published.isoformat()

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc artykulu MON254.")
    async def test_published_time_falls_back_to_page_created_at_when_published_at_is_null(self, mock_get_markdown, client, db_session):
        """Wpis bez published_at: article:published_time to created_at strony (szablon page.html)."""
        project = await _create_project(db_session, "blog-meta-nodate")
        user = await _create_user(db_session, "blog-meta-nodate@test.com")
        created = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Bez Daty MON254", is_public=True, published_at=None, created_at=created)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert f'property="article:published_time" content="{created.isoformat()}"' in resp.text


@pytest.mark.integration
class TestLandingOgTypeStillWebsite:
    """Regresja: strony spoza /blog/ (np. landing) NADAL maja og:type=website --
    dodanie og:type=article dla wpisow bloga nie moze zepsuc reszty serwisu."""

    async def test_landing_page_og_type_is_still_website(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_LANDING_PAGE", False)

        resp = await client.get("/?lang=en")

        assert resp.status_code == 200
        assert 'property="og:type" content="website"' in resp.text


# ---------------------------------------------------------------------------
# MON-254: stabilny slug wpisu, 301 ze starego adresu, sortowanie po published_at,
# zajawka z description, meta / OG z metadanych, wejscia ustawiajace is_public.
# ---------------------------------------------------------------------------


@contextmanager
def _wiki_side_effects():
    """Odcina MinIO, embeddingi i backlinki dla create/update_wiki_page wolanych wprost w tescie."""
    with (
        patch("monolynx.services.wiki.upload_markdown", return_value="blog-mon254/x.md"),
        patch("monolynx.services.embeddings.update_page_embeddings"),
        patch("monolynx.services.wiki.sync_backlinks"),
    ):
        yield


async def _add_owner(db_session, project, user):
    """Helper: czlonek 'owner' projektu (set_post_meta wymaga wiki:write)."""
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    await db_session.flush()


async def _blog_post_of(db, page_id):
    return (await db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one_or_none()


@pytest.mark.integration
class TestBlogRequiresPost:
    """Brak fallbacku po slugu wiki: strona publiczna bez wiersza BlogPost nie jest wpisem."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc.")
    async def test_public_page_without_blog_post_returns_404(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-nopost")
        user = await _create_user(db_session, "blog-nopost@test.com")
        page = await _create_wiki_page(db_session, project, user, "Publiczna Bez Wpisu MON254", is_public=True, with_post=False)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 404

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc.")
    async def test_public_page_without_blog_post_is_not_listed(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-nopost-list")
        user = await _create_user(db_session, "blog-nopost-list@test.com")
        await _create_wiki_page(db_session, project, user, "Publiczna Bez Wpisu Lista MON254", is_public=True, with_post=False)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Publiczna Bez Wpisu Lista MON254" not in resp.text


@pytest.mark.integration
class TestBlogStableSlug:
    """Kryterium 67123d01 (pierwsza polowa): zmiana tytulu strony nie zmienia adresu wpisu."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254 stabilny adres.")
    async def test_title_change_via_update_wiki_page_keeps_old_address_working(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-stable")
        user = await _create_user(db_session, "blog-stable@test.com")
        with _wiki_side_effects():
            page = await create_wiki_page(
                project_id=project.id,
                project_slug=project.slug,
                title="Stabilny Adres MON254",
                content="# T",
                user_id=user.id,
                is_public=True,
                db=db_session,
            )
            old_slug = page.slug
            await update_wiki_page(page=page, project_slug=project.slug, title="Zupelnie Nowy Tytul MON254", user_id=user.id, db=db_session)
        assert page.slug != old_slug

        resp = await client.get(f"/blog/{old_slug}")

        assert resp.status_code == 200

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_new_wiki_slug_after_title_change_is_not_an_address(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-stable-new")
        user = await _create_user(db_session, "blog-stable-new@test.com")
        with _wiki_side_effects():
            page = await create_wiki_page(
                project_id=project.id,
                project_slug=project.slug,
                title="Stary Tytul Adres MON254",
                content="# T",
                user_id=user.id,
                is_public=True,
                db=db_session,
            )
            await update_wiki_page(page=page, project_slug=project.slug, title="Inny Tytul Adres MON254", user_id=user.id, db=db_session)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 404


@pytest.mark.integration
class TestBlogSlugRedirect:
    """Kryterium 67123d01 (druga polowa): jawna zmiana sluga przez set_post_meta -> 301 ze starego adresu."""

    async def _published_post_with_new_slug(self, db_session, tag):
        project = await _create_project(db_session, f"blog-redir-{tag}")
        user = await _create_user(db_session, f"blog-redir-{tag}@test.com")
        await _add_owner(db_session, project, user)
        page = await _create_wiki_page(db_session, project, user, f"Wpis Redirect {tag} MON254", is_public=True)
        old_slug = page.slug
        new_slug = f"nowy-adres-{tag}-mon254"
        await blog_service.set_post_meta(db_session, user, project, page.id, slug=new_slug)
        return project, user, page, old_slug, new_slug

    async def test_old_slug_redirects_301_to_new_address(self, client, db_session):
        _project, _user, _page, old_slug, new_slug = await self._published_post_with_new_slug(db_session, "a")

        resp = await client.get(f"/blog/{old_slug}")

        assert resp.status_code == 301
        assert urlsplit(resp.headers["location"]).path == f"/blog/{new_slug}"

    async def test_redirect_keeps_query_string(self, client, db_session):
        _project, _user, _page, old_slug, _new_slug = await self._published_post_with_new_slug(db_session, "b")

        resp = await client.get(f"/blog/{old_slug}?lang=pl")

        assert resp.status_code == 301
        assert urlsplit(resp.headers["location"]).query == "lang=pl"

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc pod nowym adresem MON254.")
    async def test_new_slug_returns_200(self, mock_get_markdown, client, db_session):
        _project, _user, _page, _old_slug, new_slug = await self._published_post_with_new_slug(db_session, "c")

        resp = await client.get(f"/blog/{new_slug}")

        assert resp.status_code == 200
        assert "Tresc pod nowym adresem MON254." in resp.text

    async def test_old_slug_of_unpublished_post_returns_404_not_redirect(self, client, db_session):
        _project, _user, page, old_slug, new_slug = await self._published_post_with_new_slug(db_session, "d")
        page.is_public = False
        await db_session.flush()

        old_resp = await client.get(f"/blog/{old_slug}")
        new_resp = await client.get(f"/blog/{new_slug}")

        assert old_resp.status_code == 404
        assert new_resp.status_code == 404

    async def test_attachment_under_old_slug_redirects_301_to_current_slug_with_nosniff(self, client, db_session):
        """MON-271: obraz wstawiony przed zmianą sluga nie może się wysypać, więc stary adres załącznika daje 301."""
        _project, _user, page, old_slug, new_slug = await self._published_post_with_new_slug(db_session, "e")
        att = await _create_attachment(db_session, page, filename="stary.png", mime_type="image/png")

        resp = await client.get(f"/blog/{old_slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 301
        assert urlsplit(resp.headers["location"]).path == f"/blog/{new_slug}/attachments/{att.id}/stary.png"
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_attachment_redirect_keeps_a_filename_that_needs_quoting(self, client, db_session):
        _project, _user, page, old_slug, new_slug = await self._published_post_with_new_slug(db_session, "e2")
        att = await _create_attachment(db_session, page, filename="zdjecie z kropka.png", mime_type="image/png")

        resp = await client.get(f"/blog/{old_slug}/attachments/{att.id}/zdjecie%20z%20kropka.png")

        assert resp.status_code == 301
        assert resp.headers["location"] == f"/blog/{new_slug}/attachments/{att.id}/zdjecie%20z%20kropka.png"

    async def test_attachment_under_old_slug_of_unpublished_post_returns_404_with_nosniff(self, client, db_session):
        _project, _user, page, old_slug, _new_slug = await self._published_post_with_new_slug(db_session, "e3")
        att = await _create_attachment(db_session, page, filename="prywatny.png", mime_type="image/png")
        page.is_public = False
        await db_session.flush()

        resp = await client.get(f"/blog/{old_slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 404
        assert resp.headers.get("x-content-type-options") == "nosniff"

    @patch("monolynx.blog.minio_get_attachment", return_value=(b"\x89PNG\r\n\x1a\n", "image/png"))
    async def test_attachment_under_new_slug_is_served(self, mock_get_att, client, db_session):
        _project, _user, page, _old_slug, new_slug = await self._published_post_with_new_slug(db_session, "f")
        att = await _create_attachment(db_session, page, filename="nowy.png", mime_type="image/png")

        resp = await client.get(f"/blog/{new_slug}/attachments/{att.id}/{att.filename}")

        assert resp.status_code == 200
        assert resp.content == b"\x89PNG\r\n\x1a\n"
        assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.integration
class TestBlogSlugShapeReturns404:
    """MON-271: slug o złym kształcie (bajt NUL) to 404 na każdej trasie, nigdy 500 z PostgreSQL."""

    async def test_page_with_nul_slug_returns_404(self, client):
        resp = await client.get("/blog/%00")

        assert resp.status_code == 404

    async def test_markdown_twin_with_nul_slug_returns_404(self, client):
        resp = await client.get("/blog/%00.md")

        assert resp.status_code == 404

    async def test_attachment_with_nul_slug_returns_404_with_nosniff(self, client):
        resp = await client.get(f"/blog/%00/attachments/{uuid.uuid4()}/plik.png")

        assert resp.status_code == 404
        assert resp.headers.get("x-content-type-options") == "nosniff"

    @pytest.mark.parametrize(
        "path",
        ["/blog/%00", "/blog/%00.md", f"/blog/%00/attachments/{uuid.UUID(int=271)}/plik.png"],
        ids=["page", "md", "attachment"],
    )
    async def test_nul_slug_never_reaches_the_slug_queries(self, client, path):
        """Kształt sluga odrzuca `get_public_post_by_slug` przed zapytaniem - jedno miejsce dla trzech tras."""
        with patch("sqlalchemy.ext.asyncio.AsyncSession.execute", new_callable=AsyncMock) as mock_execute:
            resp = await client.get(path)

        assert resp.status_code == 404
        mock_execute.assert_not_awaited()


@pytest.mark.integration
class TestPublicPageRendersMarkdownOnce:
    """MON-271: `render_post_markdown` jest liczone raz na żądanie i zasila `word_count` oraz czas czytania."""

    async def test_post_page_renders_the_markdown_text_exactly_once(self, client, db_session):
        project = await _create_project(db_session, "blog-render-once")
        user = await _create_user(db_session, "blog-render-once@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Jedno Renderowanie MON271", is_public=True)

        with (
            patch("monolynx.services.wiki.get_markdown", return_value=" ".join(["slowo"] * 201)),
            patch("monolynx.blog.render_post_markdown", wraps=real_render_post_markdown) as spy,
        ):
            resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert spy.call_count == 1

    async def test_word_count_and_reading_time_still_come_from_the_single_render(self, client, db_session):
        project = await _create_project(db_session, "blog-render-values")
        user = await _create_user(db_session, "blog-render-values@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Wartosci Renderowania MON271", is_public=True)

        with patch("monolynx.services.wiki.get_markdown", return_value=" ".join(["slowo"] * 201)):
            resp = await client.get(f"/blog/{page.slug}", params={"lang": "en"})

        assert re.search(r'"wordCount":\s*201\b', resp.text)
        assert "2 min read" in resp.text


@pytest.mark.integration
class TestBlogIndexOrderAndSummary:
    """Kryterium de5b0078 i 6b6abd6c: kolejnosc po published_at, zajawka z description, degradacja MinIO."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc do zajawki MON254.")
    async def test_order_follows_published_at_not_updated_at(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-order")
        user = await _create_user(db_session, "blog-order@test.com")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        # Wstawiane: srodkowy, najnowszy wg published_at, najstarszy; updated_at odwrotnie niz published_at.
        for title, published_day, updated_day in [
            ("Wpis Srodkowy Order MON254", 5, 5),
            ("Wpis Najnowszy Order MON254", 9, 1),
            ("Wpis Najstarszy Order MON254", 1, 9),
        ]:
            await _create_wiki_page(
                db_session,
                project,
                user,
                title,
                is_public=True,
                published_at=base + timedelta(days=published_day),
                updated_at=base + timedelta(days=updated_day),
            )

        resp = await client.get("/blog/")

        text = resp.text
        assert text.index("Wpis Najnowszy Order MON254") < text.index("Wpis Srodkowy Order MON254") < text.index("Wpis Najstarszy Order MON254")

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc do zajawki MON254.")
    async def test_editing_older_post_content_does_not_reorder_list(self, mock_get_markdown, client, db_session):
        """Kryterium de5b0078: edycja tresci starszego wpisu nie przesuwa go na gore listy."""
        project = await _create_project(db_session, "blog-edit-order")
        user = await _create_user(db_session, "blog-edit-order@test.com")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        older = await _create_wiki_page(db_session, project, user, "Starszy Wpis Edycja MON254", is_public=True, published_at=base)
        await _create_wiki_page(db_session, project, user, "Nowszy Wpis Edycja MON254", is_public=True, published_at=base + timedelta(days=30))
        with _wiki_side_effects():
            await update_wiki_page(page=older, project_slug=project.slug, content="Nowa tresc starszego wpisu.", user_id=user.id, db=db_session)
        # now() w transakcji testu jest stale, wiec czas edycji ustawiamy jawnie na najswiezszy.
        older.updated_at = base + timedelta(days=365)
        await db_session.flush()

        resp = await client.get("/blog/")

        assert resp.text.index("Nowszy Wpis Edycja MON254") < resp.text.index("Starszy Wpis Edycja MON254")

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc do zajawki MON254.")
    async def test_list_shows_published_at_date_not_updated_at(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-date")
        user = await _create_user(db_session, "blog-date@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Data MON254",
            is_public=True,
            published_at=datetime(2026, 3, 14, 10, 0, tzinfo=UTC),
            updated_at=datetime(2027, 7, 7, 10, 0, tzinfo=UTC),
        )

        resp = await client.get("/blog/")

        assert "2026-03-14" in resp.text
        assert "2027-07-07" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc do zajawki MON254.")
    async def test_list_shows_page_created_at_date_when_published_at_is_null(self, mock_get_markdown, client, db_session):
        """Wpis bez published_at pokazuje na liscie date utworzenia strony (fallback w blog.py)."""
        project = await _create_project(db_session, "blog-date-null")
        user = await _create_user(db_session, "blog-date-null@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Bez Daty Publikacji MON254",
            is_public=True,
            published_at=None,
            created_at=datetime(2025, 6, 15, 12, 0, tzinfo=UTC),
        )

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Wpis Bez Daty Publikacji MON254" in resp.text
        assert "2025-06-15" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc zastepcza MON254-FALLBACK.")
    async def test_summary_comes_from_description_not_from_content(self, mock_get_markdown, client, db_session):
        """Zajawka to `description`; treść jest czytana tylko do czasu czytania i nie trafia do zajawki (MON-260)."""
        project = await _create_project(db_session, "blog-desc")
        user = await _create_user(db_session, "blog-desc@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Z Opisem MON254", is_public=True, description="Opis z metadanych MON254-DESC.")

        resp = await client.get("/blog/")

        assert "Opis z metadanych MON254-DESC." in resp.text
        assert "MON254-FALLBACK" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="# Naglowek\n\nZajawka z tresci MON254-FALLBACK.")
    async def test_empty_description_falls_back_to_content_summary(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-desc-empty")
        user = await _create_user(db_session, "blog-desc-empty@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Bez Opisu MON254", is_public=True, description="")

        resp = await client.get("/blog/")

        assert "Zajawka z tresci MON254-FALLBACK." in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="> [!TLDR]\n> Zajawka z alertu MON255-TLDR.\n\nReszta wpisu.")
    async def test_list_summary_skips_alert_marker_of_leading_alert(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-list-tldr")
        user = await _create_user(db_session, "blog-list-tldr@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Z Alertem Lista MON255", is_public=True)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Zajawka z alertu MON255-TLDR." in resp.text
        assert "[!TLDR]" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_minio_error_for_one_post_degrades_only_that_summary(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-minio-one")
        user = await _create_user(db_session, "blog-minio-one@test.com")
        broken_path = f"{project.slug}/broken-{uuid.uuid4()}.md"

        def fake_get_markdown(path):
            if path == broken_path:
                raise Exception("MinIO niedostepne")
            return "Zajawka sprawnego wpisu MON254-OK."

        mock_get_markdown.side_effect = fake_get_markdown
        await _create_wiki_page(db_session, project, user, "Wpis Zepsuty MinIO MON254", minio_path=broken_path, is_public=True)
        await _create_wiki_page(db_session, project, user, "Wpis Sprawny MinIO MON254", is_public=True)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Wpis Zepsuty MinIO MON254" in resp.text
        assert "Wpis Sprawny MinIO MON254" in resp.text
        assert "Zajawka sprawnego wpisu MON254-OK." in resp.text

    @patch("monolynx.services.wiki.get_markdown", side_effect=Exception("MinIO niedostepne"))
    async def test_minio_error_for_all_posts_still_returns_200_with_titles(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-minio-all")
        user = await _create_user(db_session, "blog-minio-all@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Jeden MinIO MON254", is_public=True)
        await _create_wiki_page(db_session, project, user, "Wpis Dwa MinIO MON254", is_public=True)

        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert "Wpis Jeden MinIO MON254" in resp.text
        assert "Wpis Dwa MinIO MON254" in resp.text


@pytest.mark.integration
class TestBlogPageMetaFromBlogPost:
    """Strona wpisu: meta description / OG z description, og:image z okladki."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc zastepcza MON254-FALLBACK.")
    async def test_meta_and_og_description_come_from_post_description(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-meta-desc")
        user = await _create_user(db_session, "blog-meta-desc@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Opis MON254", is_public=True, description="Opis metadanych MON254 ZXC")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert '<meta name="description" content="Opis metadanych MON254 ZXC">' in resp.text
        assert 'property="og:description" content="Opis metadanych MON254 ZXC"' in resp.text
        assert "MON254-FALLBACK" not in resp.text.split("<body", 1)[0]

    @patch("monolynx.services.wiki.get_markdown", return_value="Pierwsza-linia-MON254-FALLBACK.")
    async def test_empty_description_falls_back_to_content_in_meta(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-meta-fallback")
        user = await _create_user(db_session, "blog-meta-fallback@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Fallback MON254", is_public=True, description="")

        resp = await client.get(f"/blog/{page.slug}")

        assert 'property="og:description" content="Pierwsza-linia-MON254-FALLBACK."' in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_og_and_twitter_image_use_cover_attachment_absolute_url(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-meta-cover")
        user = await _create_user(db_session, "blog-meta-cover@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Okladka MON254", is_public=True)
        att = await _create_attachment(db_session, page, filename="okladka.png", mime_type="image/png")
        post = await db_session.get(BlogPost, page.id)
        post.cover_attachment_id = att.id
        await db_session.flush()
        expected = f"{settings.APP_URL.rstrip('/')}/blog/{page.slug}/attachments/{att.id}/okladka.png"

        resp = await client.get(f"/blog/{page.slug}")

        assert f'property="og:image" content="{expected}"' in resp.text
        assert f'name="twitter:image" content="{expected}"' in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_og_image_stays_layout_default_without_cover(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-meta-nocover")
        user = await _create_user(db_session, "blog-meta-nocover@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Meta Bez Okladki MON254", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert f'property="og:image" content="{settings.APP_URL}/static/img/screenshots/social-media.png"' in resp.text


@pytest.mark.integration
class TestBlogPageLinksUseBlogPostSlug:
    """Okruszki, podstrony i backlinki linkuja po BlogPost.slug (nie po slugu strony wiki)."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_children_link_by_post_slug(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-links-child")
        user = await _create_user(db_session, "blog-links-child@test.com")
        parent = await _create_wiki_page(db_session, project, user, "Rodzic Linki MON254", is_public=True)
        child = await _create_wiki_page(
            db_session, project, user, "Podstrona Linki MON254", parent_id=parent.id, is_public=True, post_slug="adres-podstrony-mon254"
        )

        resp = await client.get(f"/blog/{parent.slug}")

        assert "/blog/adres-podstrony-mon254" in resp.text
        assert f"/blog/{child.slug}" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_breadcrumbs_link_by_post_slug(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-links-crumb")
        user = await _create_user(db_session, "blog-links-crumb@test.com")
        parent = await _create_wiki_page(db_session, project, user, "Rodzic Okruszki MON254", is_public=True, post_slug="adres-rodzica-mon254")
        child = await _create_wiki_page(db_session, project, user, "Dziecko Okruszki MON254", parent_id=parent.id, is_public=True)

        resp = await client.get(f"/blog/{child.slug}")

        assert "/blog/adres-rodzica-mon254" in resp.text
        assert f"/blog/{parent.slug}" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON254.")
    async def test_backlinks_link_by_post_slug(self, mock_get_markdown, client, db_session):
        project = await _create_project(db_session, "blog-links-back")
        user = await _create_user(db_session, "blog-links-back@test.com")
        target = await _create_wiki_page(db_session, project, user, "Cel Backlink MON254", is_public=True)
        linker = await _create_wiki_page(db_session, project, user, "Linker Backlink MON254", is_public=True, post_slug="adres-linkera-mon254")
        db_session.add(WikiBacklink(source_page_id=linker.id, target_page_id=target.id, anchor_text="Linker Backlink MON254"))
        await db_session.flush()

        resp = await client.get(f"/blog/{target.slug}")

        assert "/blog/adres-linkera-mon254" in resp.text
        assert f"/blog/{linker.slug}" not in resp.text


# ---------------------------------------------------------------------------
# Kryterium bcc8491b: is_public=true na stronie bez metadanych tworzy BlogPost
# z wartosciami domyslnymi przez KAZDE z trzech wejsc (dashboard, MCP, API v2).
# ---------------------------------------------------------------------------


def _assert_default_blog_post(post, expected_slug):
    assert post is not None
    assert post.slug == expected_slug
    assert post.lang == "pl"
    assert post.author_name == "Zespół Monolynx"
    assert post.description == ""
    assert post.tags == []
    assert post.previous_slugs == []
    assert post.published_at is not None


@asynccontextmanager
async def _mcp_env(engine, monkeypatch, *, private_page_title=None):
    """Prawdziwe zapisy na osobnych sesjach (jak MON-203): wynik czytany w NOWEJ sesji dowodzi commita.

    Sprzata wszystko w finally (BlogPost znika kaskada razem ze strona).
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "async_session_factory", factory)
    uid = uuid.uuid4().hex[:8]
    raw_token, token_hash = generate_api_token()
    page_id = None

    async with factory() as setup_db:
        user = User(email=f"blog-mcp-{uid}@test.com", is_superuser=False, can_edit_blog=True)
        setup_db.add(user)
        await setup_db.flush()
        setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="mon254-mcp"))
        project = Project(
            name=f"Blog MCP {uid}",
            slug=f"blog-mcp-{uid}",
            code=f"BM{uid[:4].upper()}",
            api_key=f"key-{uuid.uuid4().hex}",
            is_active=True,
        )
        setup_db.add(project)
        await setup_db.flush()
        setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        if private_page_title is not None:
            page = WikiPage(
                project_id=project.id,
                title=private_page_title,
                slug=f"blog-mcp-prywatna-{uid}",
                position=0,
                minio_path=f"{project.slug}/{uid}.md",
                is_public=False,
                created_by_id=user.id,
                last_edited_by_id=user.id,
            )
            setup_db.add(page)
            await setup_db.flush()
            page_id = page.id
        await setup_db.commit()
        user_id, project_id, project_slug = user.id, project.id, project.slug

    ctx = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {raw_token}"}
    try:
        yield factory, ctx, project_slug, page_id
    finally:
        async with factory() as cleanup_db:
            await cleanup_db.execute(delete(WikiPage).where(WikiPage.project_id == project_id))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.id == user_id))
            await cleanup_db.commit()


@pytest.mark.integration
class TestPublishCreatesBlogPost:
    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon254-dash-edit/fake.md")
    async def test_dashboard_edit_form_with_is_public_creates_default_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "mon254-dash-edit")
        await login_session(client, db_session, email="mon254-dash-edit@test.com")
        user = (await db_session.execute(select(User).where(User.email == "mon254-dash-edit@test.com"))).scalar_one()
        await _add_owner(db_session, project, user)
        page = await _create_wiki_page(db_session, project, user, "Dashboard Edycja MON254", is_public=False)
        assert await _blog_post_of(db_session, page.id) is None

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/{page.id}/edit",
            data={"title": "Dashboard Edycja MON254", "content": "Tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        _assert_default_blog_post(await _blog_post_of(db_session, page.id), page.slug)

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon254-dash-create/fake.md")
    async def test_dashboard_create_form_with_is_public_creates_default_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "mon254-dash-create")
        await login_session(client, db_session, email="mon254-dash-create@test.com")
        user = (await db_session.execute(select(User).where(User.email == "mon254-dash-create@test.com"))).scalar_one()
        await _add_owner(db_session, project, user)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Dashboard Nowa MON254", "content": "Tresc", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        page = (
            await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Dashboard Nowa MON254"))
        ).scalar_one()
        _assert_default_blog_post(await _blog_post_of(db_session, page.id), page.slug)

    @patch("monolynx.services.embeddings.update_page_embeddings")
    @patch("monolynx.services.wiki.upload_markdown", return_value="mon254-dash-private/fake.md")
    async def test_dashboard_create_form_without_is_public_creates_no_blog_post(self, mock_upload, mock_embeddings, client, db_session):
        project = await _create_project(db_session, "mon254-dash-private")
        await login_session(client, db_session, email="mon254-dash-private@test.com")
        user = (await db_session.execute(select(User).where(User.email == "mon254-dash-private@test.com"))).scalar_one()
        await _add_owner(db_session, project, user)

        resp = await client.post(
            f"/dashboard/{project.slug}/wiki/pages/create",
            data={"title": "Dashboard Prywatna MON254", "content": "Tresc", "position": "0"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        page = (
            await db_session.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.title == "Dashboard Prywatna MON254"))
        ).scalar_one()
        assert await _blog_post_of(db_session, page.id) is None

    async def test_mcp_update_wiki_page_with_is_public_creates_default_blog_post(self, engine, monkeypatch):
        async with _mcp_env(engine, monkeypatch, private_page_title="MCP Edycja MON254") as (factory, ctx, project_slug, page_id):
            with _wiki_side_effects():
                await mcp_server.update_wiki_page(ctx, project_slug, str(page_id), is_public=True)

            async with factory() as check_db:
                page = await check_db.get(WikiPage, page_id)
                _assert_default_blog_post(await _blog_post_of(check_db, page_id), page.slug)

    async def test_mcp_create_wiki_page_with_is_public_creates_default_blog_post(self, engine, monkeypatch):
        async with _mcp_env(engine, monkeypatch) as (factory, ctx, project_slug, _page_id):
            with _wiki_side_effects():
                created = await mcp_server.create_wiki_page(ctx, project_slug, "MCP Nowa MON254", "# Tresc", is_public=True)

            async with factory() as check_db:
                page = await check_db.get(WikiPage, uuid.UUID(created["id"]))
                _assert_default_blog_post(await _blog_post_of(check_db, page.id), page.slug)

    async def test_api_v2_patch_with_is_public_creates_default_blog_post(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blogpub-patch", can_edit_blog=True)
        page = await _create_wiki_page(db_session, project, actor.user, "API Edycja MON254", is_public=False)
        assert await _blog_post_of(db_session, page.id) is None

        with _wiki_side_effects():
            resp = await client.patch(f"/api/v2/projects/{project.slug}/wiki/pages/{page.id}", json={"is_public": True}, headers=actor.headers)

        assert resp.status_code == 200
        _assert_default_blog_post(await _blog_post_of(db_session, page.id), page.slug)

    async def test_api_v2_post_with_is_public_creates_default_blog_post(self, client, db_session):
        actor, project = await make_owner_project(db_session, "blogpub-post", can_edit_blog=True)

        with _wiki_side_effects():
            resp = await client.post(
                f"/api/v2/projects/{project.slug}/wiki/pages",
                json={"title": "API Nowa MON254", "content": "# Tresc", "is_public": True},
                headers=actor.headers,
            )

        assert resp.status_code == 201
        page_id = uuid.UUID(resp.json()["id"])
        page = await db_session.get(WikiPage, page_id)
        _assert_default_blog_post(await _blog_post_of(db_session, page_id), page.slug)


# ---------------------------------------------------------------------------
# MON-255: bloki wpisu na realnej trasie /blog/{slug}
# ---------------------------------------------------------------------------


async def _published_post(db_session, tag):
    project = await _create_project(db_session, f"blog-blocks-{tag}")
    user = await _create_user(db_session, f"blog-blocks-{tag}@test.com")
    return await _create_wiki_page(db_session, project, user, f"Wpis Blokowy {tag} MON255", is_public=True)


@pytest.mark.integration
class TestBlogPostBlocks:
    """Strona wpisu renderuje bloki przez render_post_html, nie przez samo nh3."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_alert_renders_callout_aside_without_marker(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "> [!NOTE]\n> Tresc MON255 alertu."
        page = await _published_post(db_session, "alert")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert '<aside class="blog-callout blog-callout-note">' in resp.text
        assert "Tresc MON255 alertu." in resp.text
        assert "[!NOTE]" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_headings_get_anchor_ids(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "## Wstęp MON255\n\ntekst\n\n## Wstęp MON255\n\ntekst"
        page = await _published_post(db_session, "ids")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'id="wstep-mon255"' in resp.text
        assert 'id="wstep-mon255-2"' in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_titled_code_block_and_captioned_figure_are_rendered(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = '```python title="app/mon255.py"\nprint(1)\n```\n\n![Opis](https://example.com/mon255.png "Podpis MON255")'
        page = await _published_post(db_session, "code")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert '<span class="blog-code-title">app/mon255.py</span>' in resp.text
        assert 'class="language-python"' in resp.text
        assert "<figcaption>Podpis MON255</figcaption>" in resp.text
        assert 'loading="lazy"' in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_page_links_block_styles_and_script(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc MON255."
        page = await _published_post(db_session, "assets")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "/static/blog/blocks.css" in resp.text
        assert "/static/blog/blocks.js" in resp.text

    @pytest.mark.parametrize(
        ("path", "content_type_part"),
        [("/static/blog/blocks.css", "css"), ("/static/blog/blocks.js", "javascript")],
    )
    async def test_block_assets_are_served(self, path, content_type_part, client):
        resp = await client.get(path)

        assert resp.status_code == 200
        assert content_type_part in resp.headers["content-type"]
        assert len(resp.content) > 0

    @patch("monolynx.services.wiki.get_markdown")
    async def test_xss_and_hand_written_block_classes_do_not_reach_the_page(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = (
            "Artykul MON255.\n\n"
            "<script>alert('mon255-xss')</script>\n\n"
            '<div class="blog-callout-note">mon255-reczny</div>\n\n'
            '<h2 id="hack255">Reczny naglowek</h2>\n\n'
            "[Link](javascript:alert(2))\n\n"
            "> [!NOTE]\n> <script>alert('mon255-alert-xss')</script> Tresc."
        )
        page = await _published_post(db_session, "xss")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "mon255-xss" not in resp.text
        assert "mon255-alert-xss" not in resp.text
        assert 'class="blog-callout-note"' not in resp.text
        assert 'id="hack255"' not in resp.text
        assert "javascript:" not in resp.text
        # Pozytyw obok negatywu: prawdziwy alert z tego samego wpisu nadal ma klase z renderera.
        assert '<aside class="blog-callout blog-callout-note">' in resp.text
        assert "mon255-reczny" in resp.text

    @pytest.mark.parametrize(
        ("url", "provider", "video_id"),
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ"),
            ("https://vimeo.com/123456789", "vimeo", "123456789"),
        ],
    )
    @patch("monolynx.services.wiki.get_markdown")
    async def test_video_block_renders_placeholder_without_iframe(self, mock_get_markdown, url, provider, video_id, client, db_session):
        mock_get_markdown.return_value = f":::video\n{url}\n\nOpis MON256 wideo.\n:::"
        page = await _published_post(db_session, f"video-{provider}")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'class="blog-video"' in resp.text
        assert f'data-provider="{provider}"' in resp.text
        assert f'data-video-id="{video_id}"' in resp.text
        assert f'href="{url}"' in resp.text
        assert "Opis MON256 wideo." in resp.text
        assert "<iframe" not in resp.text.lower()
        assert "ytimg" not in resp.text
        assert "vimeocdn" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_video_from_host_outside_whitelist_renders_plain_link_without_video_markup(self, mock_get_markdown, client, db_session):
        url = "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ"
        mock_get_markdown.return_value = f":::video\n{url}\n\nOpis MON256 obcego hosta.\n:::"
        page = await _published_post(db_session, "video-foreign")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "blog-video" not in resp.text
        assert "data-video-id" not in resp.text
        assert "<iframe" not in resp.text.lower()
        assert f'href="{url}"' in resp.text
        assert "Opis MON256 obcego hosta." in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_faq_with_three_questions_renders_three_details_items(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = (
            ":::faq\n"
            "### Pytanie MON256 pierwsze?\nOdpowiedź pierwsza.\n\n"
            "### Pytanie MON256 drugie?\nOdpowiedź druga.\n\n"
            "### Pytanie MON256 trzecie?\nOdpowiedź trzecia.\n"
            ":::"
        )
        page = await _published_post(db_session, "faq")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count('class="blog-faq-item"') == 3
        assert resp.text.count('class="blog-faq-question"') == 3
        for question in ("pierwsze", "drugie", "trzecie"):
            assert f"Pytanie MON256 {question}?" in resp.text
        assert ":::" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_xss_in_details_title_and_raw_iframe_do_not_reach_the_page(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = (
            ":::details <script>alert('mon256-xss')</script><img src=x onerror=alert('mon256-img')>\n"
            'Treść MON256 ukryta.\n\n<iframe src="https://evil.example/mon256"></iframe>\n'
            ":::"
        )
        page = await _published_post(db_session, "details-xss")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "<script>alert(" not in resp.text
        assert "<img src=x" not in resp.text
        assert "<iframe" not in resp.text.lower()
        assert "evil.example" not in resp.text
        # Pozytyw obok negatywu: blok details nadal sie renderuje, a tresc zostaje.
        assert 'class="blog-details"' in resp.text
        assert "Treść MON256 ukryta." in resp.text


# ---------------------------------------------------------------------------
# MON-257: bloki graficzne (chart, diagram, terminal, graphic) na realnej trasie /blog/{slug},
# w bliźniaku .md i w plikach statycznych komponentow
# ---------------------------------------------------------------------------

_MERMAID_SCRIPT = re.compile(
    r'<script\b[^>]*\bsrc="https://cdn\.jsdelivr\.net/npm/mermaid@11\.17\.2/dist/mermaid\.min\.js"[^>]*\bdefer\b[^>]*></script>',
)
_CHART_SVG_TAG = re.compile(r'<svg\b[^>]*\bclass="blog-chart-svg"[^>]*>')
_CHART_BLOCK = ':::chart type="bar" title="Tickety MON257"\n| Miesiąc | Tickety |\n|---|---|\n| Styczeń | 12 |\n| Luty | 18 |\n:::'
_DIAGRAM_BLOCK = ':::diagram title="Obieg MON257"\nOpis diagramu MON257 w kilku słowach.\n\n```mermaid\ngraph LR\nA[Ticket] --> B[Agent]\n```\n:::'
_TERMINAL_BLOCK = ':::terminal title="Logowanie MON257"\n```console\n$ monolynx auth login\nZalogowano jako piotr@example.com\n```\n:::'
_GRAPHIC_BLOCK = ':::graphic name="agent-loop"\nOpis grafiki MON257: ticket, agent, MR i wiki.\n:::'
_GRAPHICS_DIR = "/static/blog/graphics/"


def _twin_body(text: str) -> str:
    """Treść bliźniaka `.md` bez frontmatter."""
    assert text.startswith("---\n")
    return text[text.index("\n---\n", 4) + len("\n---\n") :]


@pytest.mark.integration
class TestBlogGraphicBlocks:
    @patch("monolynx.services.wiki.get_markdown")
    async def test_post_without_a_diagram_has_no_mermaid_reference_even_with_other_graphic_blocks(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_CHART_BLOCK}\n\n{_TERMINAL_BLOCK}\n\n{_GRAPHIC_BLOCK}"
        page = await _published_post(db_session, "gfx-no-diagram")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "mermaid" not in resp.text.lower()
        assert "data-blog-diagram" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_plain_mermaid_code_block_does_not_load_the_library(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tekst MON257.\n\n```mermaid\ngraph TD\nA-->B\n```"
        page = await _published_post(db_session, "gfx-code-only")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "cdn.jsdelivr.net" not in resp.text
        assert "data-blog-diagram" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_diagram_without_a_mermaid_block_shows_the_description_and_loads_no_library(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = ':::diagram title="Opis"\nSam opis MON257 bez źródła.\n:::'
        page = await _published_post(db_session, "gfx-diagram-no-source")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "Sam opis MON257 bez źródła." in resp.text
        assert "mermaid" not in resp.text.lower()

    @patch("monolynx.services.wiki.get_markdown")
    async def test_post_with_a_diagram_loads_the_pinned_library_exactly_once(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_DIAGRAM_BLOCK}"
        page = await _published_post(db_session, "gfx-diagram")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count("cdn.jsdelivr.net/npm/mermaid@") == 1
        assert len(_MERMAID_SCRIPT.findall(resp.text)) == 1
        assert resp.text.count("data-blog-diagram") == 1
        assert "Opis diagramu MON257 w kilku słowach." in resp.text
        assert "A[Ticket] --&gt; B[Agent]" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_library_script_carries_subresource_integrity_and_anonymous_cors(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_DIAGRAM_BLOCK}"
        page = await _published_post(db_session, "gfx-diagram-sri")

        resp = await client.get(f"/blog/{page.slug}")

        tag = _MERMAID_SCRIPT.findall(resp.text)
        assert len(tag) == 1
        assert re.search(r'\bintegrity="sha384-[A-Za-z0-9+/]{64}"', tag[0])
        assert 'crossorigin="anonymous"' in tag[0]

    @patch("monolynx.services.wiki.get_markdown")
    async def test_two_diagrams_in_one_post_still_load_the_library_once(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"{_DIAGRAM_BLOCK}\n\nTekst pomiędzy.\n\n{_DIAGRAM_BLOCK.replace('MON257', 'MON257-drugi')}"
        page = await _published_post(db_session, "gfx-two-diagrams")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count("data-blog-diagram") == 2
        assert resp.text.count("cdn.jsdelivr.net/npm/mermaid@") == 1
        assert len(_MERMAID_SCRIPT.findall(resp.text)) == 1

    @patch("monolynx.services.wiki.get_markdown")
    async def test_diagram_marker_inside_a_code_fence_does_not_load_the_library(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"````\n{_DIAGRAM_BLOCK}\n````"
        page = await _published_post(db_session, "gfx-fenced-diagram")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "cdn.jsdelivr.net" not in resp.text
        assert "data-blog-diagram" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_registered_graphic_links_its_own_script_and_style_once_and_shows_the_description(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_GRAPHIC_BLOCK}"
        page = await _published_post(db_session, "gfx-graphic")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count(f"{_GRAPHICS_DIR}agent-loop.js") == 1
        assert resp.text.count(f"{_GRAPHICS_DIR}agent-loop.css") == 1
        styles = re.findall(rf'<link\b[^>]*href="{re.escape(_GRAPHICS_DIR)}agent-loop\.css"[^>]*>', resp.text)
        scripts = re.findall(rf'<script\b[^>]*src="{re.escape(_GRAPHICS_DIR)}agent-loop\.js"[^>]*>', resp.text)
        assert len(styles) == 1
        assert 'rel="stylesheet"' in styles[0]
        assert len(scripts) == 1
        assert "defer" in scripts[0]
        assert resp.text.count('data-blog-graphic="agent-loop"') == 1
        assert "Opis grafiki MON257: ticket, agent, MR i wiki." in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_graphic_used_twice_still_links_the_component_once(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"{_GRAPHIC_BLOCK}\n\nTekst pomiędzy.\n\n{_GRAPHIC_BLOCK.replace('MON257', 'MON257-drugi')}"
        page = await _published_post(db_session, "gfx-graphic-twice")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count('data-blog-graphic="agent-loop"') == 2
        assert resp.text.count(f"{_GRAPHICS_DIR}agent-loop.js") == 1
        assert resp.text.count(f"{_GRAPHICS_DIR}agent-loop.css") == 1

    @pytest.mark.parametrize(
        ("tag", "name"),
        [("unknown", "no-such-component"), ("traversal", "../../../etc/passwd"), ("case", "Agent-Loop")],
    )
    @patch("monolynx.services.wiki.get_markdown")
    async def test_graphic_outside_the_registry_shows_only_the_description(self, mock_get_markdown, tag, name, client, db_session):
        mock_get_markdown.return_value = f':::graphic name="{name}"\nOpis spoza rejestru MON257.\n:::'
        page = await _published_post(db_session, f"gfx-{tag}")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "Opis spoza rejestru MON257." in resp.text
        assert _GRAPHICS_DIR not in resp.text
        assert "data-blog-graphic" not in resp.text
        assert name not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_post_without_graphics_links_no_component_files(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_CHART_BLOCK}\n\n{_DIAGRAM_BLOCK}"
        page = await _published_post(db_session, "gfx-no-graphic")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert _GRAPHICS_DIR not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_graphic_without_description_renders_the_page_and_links_nothing(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = 'Tekst MON257.\n\n:::graphic name="agent-loop"\n:::'
        page = await _published_post(db_session, "gfx-graphic-empty")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "Tekst MON257." in resp.text
        assert _GRAPHICS_DIR not in resp.text
        assert "data-blog-graphic" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_graphic_marker_inside_a_code_fence_links_no_component_files(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"````\n{_GRAPHIC_BLOCK}\n````"
        page = await _published_post(db_session, "gfx-fenced-graphic")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert _GRAPHICS_DIR not in resp.text
        assert "data-blog-graphic" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_chart_renders_inline_svg_with_collapsible_table_and_keeps_label_markup_out(self, mock_get_markdown, client, db_session):
        label = "<script>alert('mon257-xss')</script>"
        table = f"| Miesiąc | Tickety |\n|---|---|\n| {label} | 12 |\n| Luty | 18 |"
        mock_get_markdown.return_value = f':::chart type="line" title="Tickety MON257"\n{table}\n:::'
        page = await _published_post(db_session, "gfx-chart")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'class="blog-chart blog-chart-line"' in resp.text
        svg_tags = _CHART_SVG_TAG.findall(resp.text)
        assert len(svg_tags) == 1
        assert 'role="img"' in svg_tags[0]
        assert "Dane wykresu" in resp.text
        assert "<script>alert(" not in resp.text
        assert "&lt;script&gt;alert(" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_chart_with_non_numeric_value_shows_the_table_without_svg(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = ':::chart type="bar" title="Brak liczb MON257"\n| Miesiąc | Tickety |\n|---|---|\n| Styczeń | dużo |\n:::'
        page = await _published_post(db_session, "gfx-chart-text")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "blog-chart-svg" not in resp.text
        assert "dużo" in resp.text
        assert "Dane wykresu" not in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_terminal_renders_the_full_content_in_html(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = _TERMINAL_BLOCK
        page = await _published_post(db_session, "gfx-terminal")

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert resp.text.count("data-blog-terminal") == 1
        assert 'class="blog-terminal-body"' in resp.text
        assert "$ monolynx auth login\nZalogowano jako piotr@example.com" in resp.text
        assert "Logowanie MON257" in resp.text

    @patch("monolynx.services.wiki.get_markdown")
    async def test_markdown_twin_of_all_four_blocks_carries_their_data_and_no_html(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = f"Tekst MON257.\n\n{_CHART_BLOCK}\n\n{_DIAGRAM_BLOCK}\n\n{_TERMINAL_BLOCK}\n\n{_GRAPHIC_BLOCK}"
        page = await _published_post(db_session, "gfx-twin")

        resp = await client.get(f"/blog/{page.slug}.md")

        assert resp.status_code == 200
        body = _twin_body(resp.text)
        assert re.search(r"</?[A-Za-z]", body) is None
        assert ":::" not in body
        for expected in (
            "**Tickety MON257**",
            "| Styczeń | 12 |",
            "Opis diagramu MON257 w kilku słowach.",
            "```mermaid\ngraph LR\nA[Ticket] --> B[Agent]\n```",
            "```console\n$ monolynx auth login\nZalogowano jako piotr@example.com\n```",
            "Opis grafiki MON257: ticket, agent, MR i wiki.",
        ):
            assert expected in body
        assert "agent-loop" not in body

    @pytest.mark.parametrize(
        ("path", "content_type_part"),
        [("/static/blog/graphics/agent-loop.js", "javascript"), ("/static/blog/graphics/agent-loop.css", "css")],
    )
    async def test_agent_loop_component_files_are_served(self, path, content_type_part, client):
        resp = await client.get(path)

        assert resp.status_code == 200
        assert content_type_part in resp.headers["content-type"]
        assert len(resp.content) > 0


# ---------------------------------------------------------------------------
# MON-260: szablon wpisu i listy dla ludzi
# ---------------------------------------------------------------------------

_APP = settings.APP_URL.rstrip("/")
_TEMPLATE_PAGE = Path(__file__).resolve().parents[2] / "src" / "monolynx" / "templates" / "blog" / "page.html"
_BASE_DAY = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _words(count):
    return " ".join(["slowo"] * count)


def _hrefs(text):
    return [html.unescape(href) for href in re.findall(r'href="([^"]*)"', text)]


def _head_link(text, rel):
    match = re.search(rf'<link rel="{rel}" href="([^"]*)"', text)
    return html.unescape(match.group(1)) if match else None


def _hreflangs(text):
    pairs = re.findall(r'<link[^>]*rel="alternate"[^>]*hreflang="([\w-]+)"[^>]*href="([^"]*)"', text)
    return {lang: html.unescape(href) for lang, href in pairs}


def _cards(text):
    return text.split('<article class="blog-card')[1:]


def _after(text, marker, size=400):
    assert marker in text, f"brak {marker!r} w HTML"
    return text.split(marker, 1)[1][:size]


def _section(text, opener, closer):
    assert opener in text, f"brak {opener!r} w HTML"
    return text.split(opener, 1)[1].split(closer, 1)[0]


async def _scene(db_session, tag):
    project = await _create_project(db_session, f"blog260-{tag}")
    user = await _create_user(db_session, f"blog260-{tag}@test.com")
    return project, user


async def _seed_series(db_session, project, user, count, *, prefix, tags=None, lang="pl"):
    """`count` publicznych wpisów `<prefix> 00..`, najnowszy ma najwyższy numer; opis ustawiony (bez zajawki z MinIO)."""
    for i in range(count):
        await _create_wiki_page(
            db_session,
            project,
            user,
            f"{prefix} {i:02d}",
            is_public=True,
            description="Opis serii MON260.",
            tags=tags or [],
            lang=lang,
            published_at=_BASE_DAY + timedelta(minutes=i),
        )


@pytest.mark.integration
class TestListRelatedPosts:
    """`list_related_posts` (kryterium 3): tylko publiczne, ten sam język, bez bieżącego; wspólne tagi, potem najnowsze; limit 3."""

    async def test_order_is_common_tags_desc_then_published_at_desc_limited_to_three(self, db_session):
        project, user = await _scene(db_session, "rel-order")
        day = timedelta(days=1)
        current = await _create_wiki_page(
            db_session, project, user, "Biezacy Rel Order MON260", is_public=True, tags=["a", "b", "c"], published_at=_BASE_DAY + 30 * day
        )
        # Kolejność wstawiania celowo inna niż oczekiwana: Y (3 wspólne) i Z (2) na końcu, W starszy od X przy równych tagach.
        weak_old = await _create_wiki_page(
            db_session, project, user, "Slaby Stary Rel MON260", is_public=True, tags=["a"], published_at=_BASE_DAY + 10 * day
        )
        weak_new = await _create_wiki_page(
            db_session, project, user, "Slaby Nowy Rel MON260", is_public=True, tags=["a"], published_at=_BASE_DAY + 20 * day
        )
        two = await _create_wiki_page(
            db_session, project, user, "Dwa Tagi Rel MON260", is_public=True, tags=["a", "b"], published_at=_BASE_DAY + 5 * day
        )
        three = await _create_wiki_page(
            db_session, project, user, "Trzy Tagi Rel MON260", is_public=True, tags=["a", "b", "c"], published_at=_BASE_DAY + 1 * day
        )
        # Przynęty z IDENTYCZNYMI tagami i najnowszą datą: wpis prywatny oraz wpis w innym języku.
        await _create_wiki_page(
            db_session, project, user, "Prywatny Rel MON260", is_public=False, with_post=True, tags=["a", "b", "c"], published_at=_BASE_DAY + 25 * day
        )
        await _create_wiki_page(
            db_session, project, user, "Angielski Rel MON260", is_public=True, lang="en", tags=["a", "b", "c"], published_at=_BASE_DAY + 26 * day
        )
        post = await _blog_post_of(db_session, current.id)

        result = await blog_service.list_related_posts(db_session, post)

        assert [item.post.slug for item in result] == [three.slug, two.slug, weak_new.slug]
        assert weak_old.slug not in [item.post.slug for item in result]

    async def test_limit_argument_is_respected(self, db_session):
        project, user = await _scene(db_session, "rel-limit")
        current = await _create_wiki_page(db_session, project, user, "Biezacy Rel Limit MON260", is_public=True, tags=["a"])
        for i in range(3):
            await _create_wiki_page(
                db_session, project, user, f"Kandydat Rel Limit {i} MON260", is_public=True, tags=["a"], published_at=_BASE_DAY + timedelta(days=i)
            )
        post = await _blog_post_of(db_session, current.id)

        result = await blog_service.list_related_posts(db_session, post, limit=1)

        assert [item.post.slug for item in result] == ["kandydat-rel-limit-2-mon260"]

    async def test_without_common_tags_falls_back_to_newest_same_language_posts(self, db_session):
        project, user = await _scene(db_session, "rel-fallback")
        current = await _create_wiki_page(db_session, project, user, "Biezacy Rel Fallback MON260", is_public=True, tags=["solo"])
        older = await _create_wiki_page(
            db_session, project, user, "Starszy Rel Fallback MON260", is_public=True, tags=["inne"], published_at=_BASE_DAY + timedelta(days=1)
        )
        newer = await _create_wiki_page(
            db_session, project, user, "Nowszy Rel Fallback MON260", is_public=True, tags=["inne"], published_at=_BASE_DAY + timedelta(days=2)
        )
        await _create_wiki_page(db_session, project, user, "Angielski Rel Fallback MON260", is_public=True, lang="en", tags=["solo"])
        post = await _blog_post_of(db_session, current.id)

        result = await blog_service.list_related_posts(db_session, post)

        assert [item.post.slug for item in result] == [newer.slug, older.slug]

    async def test_tagged_match_comes_first_then_filler_without_duplicate(self, db_session):
        """Jeden wpis ze wspólnym tagiem + dwa bez: dokładna lista to tagowany, potem dopełnienie od najnowszego, bez powtórki tagowanego."""
        project, user = await _scene(db_session, "rel-mixed")
        day = timedelta(days=1)
        current = await _create_wiki_page(db_session, project, user, "Biezacy Rel Mixed MON260", is_public=True, tags=["solo"])
        # Tagowany jest NAJNOWSZY, więc dopełnienie bez filtra "już wybrane" wzięłoby go drugi raz.
        await _create_wiki_page(
            db_session, project, user, "Bez Tagu Trzeci Rel Mixed MON260", is_public=True, tags=["inne"], published_at=_BASE_DAY + 3 * day
        )
        tagged = await _create_wiki_page(
            db_session, project, user, "Tagowany Rel Mixed MON260", is_public=True, tags=["solo"], published_at=_BASE_DAY + 9 * day
        )
        fill_new = await _create_wiki_page(
            db_session, project, user, "Bez Tagu Piaty Rel Mixed MON260", is_public=True, tags=["inne"], published_at=_BASE_DAY + 5 * day
        )
        fill_mid = await _create_wiki_page(
            db_session, project, user, "Bez Tagu Czwarty Rel Mixed MON260", is_public=True, tags=["inne"], published_at=_BASE_DAY + 4 * day
        )
        post = await _blog_post_of(db_session, current.id)

        result = await blog_service.list_related_posts(db_session, post)

        assert [item.post.slug for item in result] == [tagged.slug, fill_new.slug, fill_mid.slug]

    async def test_post_without_own_tags_gets_newest_posts_of_its_language(self, db_session):
        project, user = await _scene(db_session, "rel-notags")
        day = timedelta(days=1)
        current = await _create_wiki_page(db_session, project, user, "Biezacy Rel Notags MON260", is_public=True, tags=[])
        pages = [
            await _create_wiki_page(
                db_session, project, user, f"Kandydat Rel Notags {i} MON260", is_public=True, tags=["a"], published_at=_BASE_DAY + i * day
            )
            for i in range(1, 5)
        ]
        await _create_wiki_page(db_session, project, user, "Angielski Rel Notags MON260", is_public=True, lang="en", published_at=_BASE_DAY + 9 * day)
        post = await _blog_post_of(db_session, current.id)

        result = await blog_service.list_related_posts(db_session, post)

        assert [item.post.slug for item in result] == [pages[3].slug, pages[2].slug, pages[1].slug]

    async def test_current_post_is_never_related_to_itself(self, db_session):
        project, user = await _scene(db_session, "rel-self")
        current = await _create_wiki_page(db_session, project, user, "Jedyny Rel Self MON260", is_public=True, tags=["a"])
        post = await _blog_post_of(db_session, current.id)

        assert await blog_service.list_related_posts(db_session, post) == []


@pytest.mark.integration
class TestGetCoverUrls:
    """`get_cover_urls`: okładka należąca do strony wpisu daje adres załącznika; cudza okładka i brak okładki nie dają klucza."""

    async def test_own_cover_gets_url_foreign_cover_and_missing_cover_are_skipped(self, db_session):
        project, user = await _scene(db_session, "cover-map")
        page_own = await _create_wiki_page(db_session, project, user, "Okladka Wlasna MON260", is_public=True)
        page_foreign = await _create_wiki_page(db_session, project, user, "Okladka Cudza MON260", is_public=True)
        page_none = await _create_wiki_page(db_session, project, user, "Okladka Brak MON260", is_public=True)
        page_other = await _create_wiki_page(db_session, project, user, "Strona Zalacznika MON260", is_public=True)
        att_own = await _create_attachment(db_session, page_own, filename="moja okladka.png", mime_type="image/png")
        att_other = await _create_attachment(db_session, page_other, filename="obca.png", mime_type="image/png")
        post_own = await _blog_post_of(db_session, page_own.id)
        post_foreign = await _blog_post_of(db_session, page_foreign.id)
        post_none = await _blog_post_of(db_session, page_none.id)
        post_own.cover_attachment_id = att_own.id
        post_foreign.cover_attachment_id = att_other.id
        await db_session.flush()

        result = await blog_service.get_cover_urls(db_session, [post_own, post_foreign, post_none])

        assert result == {page_own.id: f"/blog/{post_own.slug}/attachments/{att_own.id}/moja%20okladka.png"}

    async def test_empty_input_gives_empty_mapping(self, db_session):
        assert await blog_service.get_cover_urls(db_session, []) == {}

    async def test_resolves_all_covers_with_a_single_query(self, db_session, engine):
        project, user = await _scene(db_session, "cover-one-query")
        posts = []
        for i in range(3):
            page = await _create_wiki_page(db_session, project, user, f"Okladka Zapytanie {i} MON260", is_public=True)
            att = await _create_attachment(db_session, page, filename=f"o{i}.png", mime_type="image/png")
            post = await _blog_post_of(db_session, page.id)
            post.cover_attachment_id = att.id
            posts.append(post)
        await db_session.flush()
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await blog_service.get_cover_urls(db_session, posts)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)

        assert len(result) == 3
        assert len(statements) == 1


@pytest.mark.integration
class TestBlogPageHeader:
    """Kryterium 1: autor, data publikacji, czas czytania, tagi, okładka i "zweryfikowano" są na stronie wpisu, gdy ustawione;
    gdy ich brak, nie ma pustych elementów."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_header_shows_author_dates_reading_time_tags_and_cover_when_set(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = _words(450)
        project, user = await _scene(db_session, "hdr-full")
        page = await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Naglowek Pelny MON260",
            is_public=True,
            author_name="Anna Autorska MON260",
            tags=["python", "fastapi"],
            published_at=datetime(2026, 1, 12, 8, 0, tzinfo=UTC),
            last_verified_at=date(2026, 2, 3),
        )
        att = await _create_attachment(db_session, page, filename="okladka.png", mime_type="image/png")
        post = await _blog_post_of(db_session, page.id)
        post.cover_attachment_id = att.id
        await db_session.flush()

        resp = await client.get(f"/blog/{page.slug}")

        text = resp.text
        assert resp.status_code == 200
        assert "Anna Autorska MON260" in _after(text, 'class="blog-author')
        assert "3 min czytania" in _after(text, 'class="blog-reading-time')
        assert re.search(r'<time class="blog-published[^"]*"\s+datetime="2026-01-12', text)
        assert "2026-02-03" in _after(text, 'class="blog-verified')
        for tag in ("python", "fastapi"):
            assert re.search(rf'<a class="blog-tag(?:\s[^"]*)?"[^>]*href="/blog/\?tag={tag}', text)
        assert re.search(rf'<img class="blog-cover[^>]*src="[^"]*/blog/{page.slug}/attachments/{att.id}/okladka\.png', text)
        assert re.search(rf'<a class="blog-md-link[^>]*href="[^"]*/blog/{page.slug}\.md"', text)

    @patch("monolynx.services.wiki.get_markdown")
    async def test_header_omits_empty_elements_when_data_is_missing(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Krotka tresc bez naglowkow."
        project, user = await _scene(db_session, "hdr-empty")
        page = await _create_wiki_page(db_session, project, user, "Wpis Naglowek Pusty MON260", is_public=True, author_name="", tags=[])

        resp = await client.get(f"/blog/{page.slug}")

        text = resp.text
        assert resp.status_code == 200
        assert 'class="blog-reading-time' in text
        for hook in ('class="blog-author', 'class="blog-tags', 'class="blog-tag"', '<img class="blog-cover', 'class="blog-verified'):
            assert hook not in text
        for hook in ('class="blog-toc', '<section class="blog-related', 'class="blog-translations', "hreflang="):
            assert hook not in text


@pytest.mark.integration
class TestBlogPageToc:
    """Kryterium 2: spis treści ma wszystkie H2 i H3, każdy link prowadzi do istniejącego `id`; poniżej 3 nagłówków go nie ma."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_toc_lists_h2_and_h3_and_every_link_targets_an_existing_id(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = (
            "## Alfa Sekcja\n\ntekst\n\n### Beta Sekcja\n\ntekst\n\n## Alfa Sekcja\n\ntekst\n\n#### Delta Sekcja\n\ntekst"
        )
        project, user = await _scene(db_session, "toc-full")
        page = await _create_wiki_page(db_session, project, user, "Wpis Spis Pelny MON260", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        text = resp.text
        assert resp.status_code == 200
        toc = _section(text, '<nav class="blog-toc', "</nav>")
        targets = re.findall(r'<a href="#([^"]+)"', toc)
        assert targets == ["alfa-sekcja", "beta-sekcja", "alfa-sekcja-2"]
        for target in targets:
            assert f'id="{target}"' in text

    @pytest.mark.parametrize(
        "markdown",
        [
            "## Alfa Sekcja\n\ntekst\n\n### Beta Sekcja\n\ntekst",
            "## Alfa Sekcja\n\ntekst\n\n### Beta Sekcja\n\ntekst\n\n#### Gamma Sekcja\n\ntekst",
        ],
        ids=["two-headings", "h4-does-not-count"],
    )
    @patch("monolynx.services.wiki.get_markdown")
    async def test_post_with_fewer_than_three_h2_h3_headings_has_no_toc(self, mock_get_markdown, markdown, client, db_session):
        mock_get_markdown.return_value = markdown
        project, user = await _scene(db_session, f"toc-short-{len(markdown)}")
        page = await _create_wiki_page(db_session, project, user, f"Wpis Spis Krotki {len(markdown)} MON260", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'class="blog-toc' not in resp.text


@pytest.mark.integration
class TestBlogPageRelated:
    """Kryterium 3 na stronie: powiązane to publiczne wpisy w tym samym języku, bez bieżącego."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_related_section_lists_only_public_same_language_posts_without_current(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "page-related")
        current = await _create_wiki_page(db_session, project, user, "Biezacy Powiazane MON260", is_public=True, tags=["t1"])
        await _create_wiki_page(db_session, project, user, "Publiczny Tagowany Powiazane MON260", is_public=True, tags=["t1"])
        await _create_wiki_page(db_session, project, user, "Publiczny Bez Tagu Powiazane MON260", is_public=True)
        await _create_wiki_page(db_session, project, user, "Prywatny Powiazane MON260", is_public=False, with_post=True, tags=["t1"])
        await _create_wiki_page(db_session, project, user, "Angielski Powiazane MON260", is_public=True, lang="en", tags=["t1"])

        resp = await client.get(f"/blog/{current.slug}")

        text = resp.text
        assert resp.status_code == 200
        related = _section(text, '<section class="blog-related', "</section>")
        assert "Publiczny Tagowany Powiazane MON260" in related
        assert "Publiczny Bez Tagu Powiazane MON260" in related
        assert "Biezacy Powiazane MON260" not in related
        assert f'href="/blog/{current.slug}"' not in related
        assert "Prywatny Powiazane MON260" not in text
        assert "Angielski Powiazane MON260" not in text


@pytest.mark.integration
class TestBlogPageTranslations:
    """Kryterium 5: wpis z publicznym tłumaczeniem ma `hreflang` obu języków i przełącznik; bez tłumaczenia (albo z prywatnym) - żadnego."""

    async def _pair(self, db_session, tag, *, translation_public=True):
        project, user = await _scene(db_session, tag)
        root = await _create_wiki_page(db_session, project, user, f"Wpis Polski Tlumaczenie {tag} MON260", is_public=True, lang="pl")
        english = await _create_wiki_page(
            db_session,
            project,
            user,
            f"Post English Translation {tag} MON260",
            is_public=translation_public,
            with_post=True,
            lang="en",
            translation_of_id=root.id,
        )
        return root, english

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_original_has_hreflang_for_both_languages_and_switcher_to_translation(self, mock_get_markdown, client, db_session):
        root, english = await self._pair(db_session, "hl-root")

        resp = await client.get(f"/blog/{root.slug}")

        text = resp.text
        assert resp.status_code == 200
        assert _hreflangs(text) == {"pl": f"{_APP}/blog/{root.slug}", "en": f"{_APP}/blog/{english.slug}"}
        assert f"/blog/{english.slug}" in _after(text, 'class="blog-translations', 800)

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_translation_has_the_same_hreflang_set_and_switcher_back_to_original(self, mock_get_markdown, client, db_session):
        root, english = await self._pair(db_session, "hl-en")

        resp = await client.get(f"/blog/{english.slug}")

        text = resp.text
        assert resp.status_code == 200
        assert _hreflangs(text) == {"pl": f"{_APP}/blog/{root.slug}", "en": f"{_APP}/blog/{english.slug}"}
        assert f"/blog/{root.slug}" in _after(text, 'class="blog-translations', 800)

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_post_without_translation_has_no_hreflang_and_no_switcher(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "hl-none")
        page = await _create_wiki_page(db_session, project, user, "Wpis Bez Tlumaczenia MON260", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert "hreflang=" not in resp.text
        assert 'class="blog-translations' not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_private_translation_gives_no_hreflang_and_does_not_leak(self, mock_get_markdown, client, db_session):
        root, english = await self._pair(db_session, "hl-private", translation_public=False)

        resp = await client.get(f"/blog/{root.slug}")

        assert resp.status_code == 200
        assert "hreflang=" not in resp.text
        assert 'class="blog-translations' not in resp.text
        assert english.slug not in resp.text


@pytest.mark.integration
class TestBlogIndexFilters:
    """Kryteria 4 i 6: filtr tagu i języka listy, paginacja zachowująca filtry, canonical/prev/next, zły tag bez 500."""

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_tag_filter_narrows_the_list(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-tag")
        await _create_wiki_page(db_session, project, user, "Z Tagiem X Filtr MON260", is_public=True, tags=["x"])
        await _create_wiki_page(db_session, project, user, "Z Tagiem Y Filtr MON260", is_public=True, tags=["y"])
        await _create_wiki_page(db_session, project, user, "Bez Tagu Filtr MON260", is_public=True)

        resp = await client.get("/blog/", params={"tag": "x"})

        assert resp.status_code == 200
        assert "Z Tagiem X Filtr MON260" in resp.text
        assert "Z Tagiem Y Filtr MON260" not in resp.text
        assert "Bez Tagu Filtr MON260" not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_empty_tag_param_means_no_filter(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-tag-empty")
        await _create_wiki_page(db_session, project, user, "Bez Tagu Pusty Filtr MON260", is_public=True)

        resp = await client.get("/blog/", params={"tag": ""})

        assert resp.status_code == 200
        assert "Bez Tagu Pusty Filtr MON260" in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_lang_filter_shows_only_posts_in_that_language(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-lang")
        await _create_wiki_page(db_session, project, user, "Wpis Polski Jezyk MON260", is_public=True, lang="pl")
        await _create_wiki_page(db_session, project, user, "Post English Language MON260", is_public=True, lang="en")

        en_resp = await client.get("/blog/", params={"lang": "en"})
        pl_resp = await client.get("/blog/", params={"lang": "pl"})

        assert "Post English Language MON260" in en_resp.text
        assert "Wpis Polski Jezyk MON260" not in en_resp.text
        assert "Wpis Polski Jezyk MON260" in pl_resp.text
        assert "Post English Language MON260" not in pl_resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_lang_param_outside_set_means_no_filter_and_english_interface(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-lang-bad")
        await _create_wiki_page(db_session, project, user, "Wpis Polski Zly Jezyk MON260", is_public=True, lang="pl")
        await _create_wiki_page(db_session, project, user, "Post English Bad Language MON260", is_public=True, lang="en")

        resp = await client.get("/blog/", params={"lang": "de"})

        assert resp.status_code == 200
        assert "Wpis Polski Zly Jezyk MON260" in resp.text
        assert "Post English Bad Language MON260" in resp.text
        assert '<html lang="en"' in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_valid_lang_param_sets_interface_language(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-lang-ui")
        await _create_wiki_page(db_session, project, user, "Wpis Polski Interfejs MON260", is_public=True, lang="pl")

        resp = await client.get("/blog/", params={"lang": "pl"})

        assert '<html lang="pl"' in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_language_switch_links_keep_the_tag_filter(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "flt-switch")
        await _create_wiki_page(db_session, project, user, "Wpis Przelacznik MON260", is_public=True, tags=["x"])

        resp = await client.get("/blog/", params={"tag": "x"})
        forced = await client.get("/blog/", params={"tag": "x", "lang": "pl"})

        hrefs = _hrefs(resp.text)
        assert "/blog/?tag=x&lang=pl" in hrefs
        assert "/blog/?tag=x&lang=en" in hrefs
        # Navbar (interfejs en) niesie link do PL z kontekstu; domyślny `/?lang=pl` layoutu oznaczałby brak `lang_switch_url`.
        assert "/?lang=pl" not in hrefs
        assert "/?lang=en" not in _hrefs(forced.text)

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_second_page_with_tag_keeps_filter_in_links_and_canonical_points_to_itself(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "pag-tag")
        await _seed_series(db_session, project, user, 41, prefix="Seria Tagowana MON260", tags=["x"])
        await _create_wiki_page(db_session, project, user, "Przyneta Bez Tagu MON260", is_public=True, published_at=_BASE_DAY + timedelta(days=5))

        resp = await client.get("/blog/", params={"tag": "x", "page": "2"})

        text = resp.text
        assert resp.status_code == 200
        assert _head_link(text, "canonical") == f"{_APP}/blog/?tag=x&page=2"
        assert _head_link(text, "prev") == f"{_APP}/blog/?tag=x"
        assert _head_link(text, "next") == f"{_APP}/blog/?tag=x&page=3"
        hrefs = _hrefs(text)
        assert "/blog/?tag=x&page=3" in hrefs
        paging = [href for href in hrefs if href.startswith("/blog/?") and "page=" in href]
        assert paging
        assert all("tag=x" in href for href in paging)
        assert "Seria Tagowana MON260 20" in text
        assert "Seria Tagowana MON260 40" not in text
        assert "Przyneta Bez Tagu MON260" not in text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_canonical_orders_query_as_tag_lang_page_regardless_of_request_order(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "pag-order")
        await _seed_series(db_session, project, user, 21, prefix="Seria Kolejnosc MON260", tags=["x"], lang="pl")
        await _seed_series(db_session, project, user, 1, prefix="Seria Angielska Kolejnosc MON260", tags=["x"], lang="en")

        resp = await client.get("/blog/?page=2&lang=pl&tag=x")

        text = resp.text
        assert resp.status_code == 200
        assert _head_link(text, "canonical") == f"{_APP}/blog/?tag=x&lang=pl&page=2"
        assert _head_link(text, "prev") == f"{_APP}/blog/?tag=x&lang=pl"
        assert "Seria Angielska Kolejnosc MON260" not in text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_first_page_without_filters_has_plain_canonical_next_but_no_prev(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "pag-first")
        await _seed_series(db_session, project, user, 21, prefix="Seria Pierwsza MON260")

        plain = await client.get("/blog/")
        explicit = await client.get("/blog/", params={"page": "1"})

        for resp in (plain, explicit):
            assert resp.status_code == 200
            assert _head_link(resp.text, "canonical") == f"{_APP}/blog/"
            assert 'rel="prev"' not in resp.text
            assert _head_link(resp.text, "next") == f"{_APP}/blog/?page=2"

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_last_page_has_prev_but_no_next(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "pag-last")
        await _seed_series(db_session, project, user, 21, prefix="Seria Ostatnia MON260")

        resp = await client.get("/blog/", params={"page": "2"})

        assert resp.status_code == 200
        assert _head_link(resp.text, "prev") == f"{_APP}/blog/"
        assert 'rel="next"' not in resp.text

    @pytest.mark.parametrize(
        "bad_tag",
        ["A B", "a\x00b", "a" * 51, "UPPER", "a;b", "-a"],
        ids=["space", "nul", "too-long", "uppercase", "semicolon", "leading-hyphen"],
    )
    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_invalid_tag_gives_empty_list_with_200(self, mock_get_markdown, bad_tag, client, db_session):
        project, user = await _scene(db_session, f"bad-tag-{len(bad_tag)}-{abs(hash(bad_tag)) % 1000}")
        await _create_wiki_page(db_session, project, user, "Wpis Zly Tag Tagowany MON260", is_public=True, tags=["x"])
        await _create_wiki_page(db_session, project, user, "Wpis Zly Tag Bez Tagu MON260", is_public=True)

        resp = await client.get("/blog/", params={"tag": bad_tag})

        assert resp.status_code == 200
        assert "No posts published yet." in resp.text
        assert "Wpis Zly Tag Tagowany MON260" not in resp.text
        assert "Wpis Zly Tag Bez Tagu MON260" not in resp.text

    async def test_invalid_tag_does_not_query_the_database_for_the_listing(self, client):
        with patch("monolynx.blog.list_public_posts", new=AsyncMock()) as mock_list:
            resp = await client.get("/blog/", params={"tag": "a\x00b"})

        assert resp.status_code == 200
        mock_list.assert_not_awaited()


def _query_of(href):
    return parse_qs(urlsplit(href).query)


def _anchor_hrefs(text, label):
    """Adresy kotwic o danym tekście (z dowolnymi atrybutami), odescapowane."""
    return [html.unescape(href) for href in re.findall(rf'<a href="([^"]*)"[^>]*>\s*{label}\s*</a>', text)]


@pytest.mark.integration
class TestBlogIndexInterfaceParam:
    """MON-271: `?ui=` to wyłącznie język interfejsu listy, `?lang=` zostaje wyłącznie filtrem języka wpisów."""

    async def _seed_both_languages(self, db_session, tag):
        project, user = await _scene(db_session, f"ui-{tag}")
        await _create_wiki_page(db_session, project, user, f"Wpis Polski Ui {tag} MON271", is_public=True, lang="pl")
        await _create_wiki_page(db_session, project, user, f"Post English Ui {tag} MON271", is_public=True, lang="en")
        return f"Wpis Polski Ui {tag} MON271", f"Post English Ui {tag} MON271"

    @pytest.mark.parametrize("ui", ["pl", "en"])
    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_ui_param_sets_interface_language_and_keeps_every_post(self, mock_get_markdown, ui, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, f"keep-{ui}")

        resp = await client.get("/blog/", params={"ui": ui})

        assert resp.status_code == 200
        assert f'<html lang="{ui}"' in resp.text
        assert pl_title in resp.text
        assert en_title in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_ui_pl_with_lang_en_gives_polish_interface_and_english_posts_only(self, mock_get_markdown, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, "plen")

        resp = await client.get("/blog/", params={"ui": "pl", "lang": "en"})

        assert resp.status_code == 200
        assert '<html lang="pl"' in resp.text
        assert en_title in resp.text
        assert pl_title not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_ui_en_with_lang_pl_gives_english_interface_and_polish_posts_only(self, mock_get_markdown, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, "enpl")

        resp = await client.get("/blog/", params={"ui": "en", "lang": "pl"})

        assert resp.status_code == 200
        assert '<html lang="en"' in resp.text
        assert pl_title in resp.text
        assert en_title not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_lang_pl_without_ui_filters_and_sets_polish_interface_as_before(self, mock_get_markdown, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, "legacy")

        resp = await client.get("/blog/", params={"lang": "pl"})

        assert resp.status_code == 200
        assert '<html lang="pl"' in resp.text
        assert pl_title in resp.text
        assert en_title not in resp.text

    @pytest.mark.parametrize("bad_ui", ["de", "", "PL", "pl\x00"], ids=["unknown", "empty", "uppercase", "nul"])
    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_invalid_ui_is_ignored_without_filtering_and_with_english_interface(self, mock_get_markdown, bad_ui, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, f"bad-{len(bad_ui)}-{abs(hash(bad_ui)) % 1000}")

        resp = await client.get("/blog/", params={"ui": bad_ui})

        assert resp.status_code == 200
        assert '<html lang="en"' in resp.text
        assert pl_title in resp.text
        assert en_title in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_invalid_ui_falls_back_to_valid_lang_for_interface_and_filter(self, mock_get_markdown, client, db_session):
        pl_title, en_title = await self._seed_both_languages(db_session, "badfallback")

        resp = await client.get("/blog/", params={"ui": "de", "lang": "pl"})

        assert resp.status_code == 200
        assert '<html lang="pl"' in resp.text
        assert pl_title in resp.text
        assert en_title not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_pagination_links_keep_ui_and_do_not_add_a_language_filter(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "ui-paging")
        await _seed_series(db_session, project, user, 41, prefix="Seria Ui Paginacja MON271", lang="pl")

        resp = await client.get("/blog/", params={"ui": "en", "page": "2"})

        assert resp.status_code == 200
        previous = _anchor_hrefs(resp.text, r"&larr; Previous")
        following = _anchor_hrefs(resp.text, r"Next &rarr;")
        assert len(previous) == 1
        assert len(following) == 1
        assert _query_of(previous[0]) == {"ui": ["en"]}
        assert _query_of(following[0]) == {"ui": ["en"], "page": ["3"]}

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_tag_links_on_cards_keep_ui_and_tag(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "ui-cardtag")
        await _create_wiki_page(db_session, project, user, "Wpis Z Tagiem Ui MON271", is_public=True, lang="pl", tags=["x"])

        resp = await client.get("/blog/", params={"ui": "pl"})

        tag_hrefs = [html.unescape(href) for href in re.findall(r'<a class="blog-tag[^"]*"[^>]*href="([^"]*)"', resp.text)]
        assert len(tag_hrefs) == 1
        assert _query_of(tag_hrefs[0]) == {"tag": ["x"], "ui": ["pl"]}

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_language_filter_links_keep_ui_and_tag(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "ui-filter")
        await _create_wiki_page(db_session, project, user, "Wpis Filtr Ui MON271", is_public=True, lang="pl", tags=["x"])

        resp = await client.get("/blog/", params={"tag": "x", "ui": "pl"})

        pl_link = re.findall(r'<a href="([^"]*)"(?: aria-current="true")?>PL</a>', resp.text)
        en_link = re.findall(r'<a href="([^"]*)"(?: aria-current="true")?>EN</a>', resp.text)
        all_link = _anchor_hrefs(resp.text, "Wszystkie")
        assert [_query_of(html.unescape(href)) for href in pl_link] == [{"tag": ["x"], "lang": ["pl"], "ui": ["pl"]}]
        assert [_query_of(html.unescape(href)) for href in en_link] == [{"tag": ["x"], "lang": ["en"], "ui": ["pl"]}]
        assert [_query_of(href) for href in all_link] == [{"tag": ["x"], "ui": ["pl"]}]

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_clear_tag_filter_link_keeps_ui_and_language_filter(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "ui-clear")
        await _create_wiki_page(db_session, project, user, "Wpis Czyszczenie Ui MON271", is_public=True, lang="pl", tags=["x"])

        resp = await client.get("/blog/", params={"tag": "x", "lang": "pl", "ui": "en"})

        clear_links = _anchor_hrefs(resp.text, "clear filter")
        assert [_query_of(href) for href in clear_links] == [{"lang": ["pl"], "ui": ["en"]}]

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON271.")
    async def test_links_without_ui_do_not_gain_one(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "ui-none")
        await _create_wiki_page(db_session, project, user, "Wpis Bez Ui MON271", is_public=True, lang="pl", tags=["x"])

        resp = await client.get("/blog/", params={"tag": "x", "lang": "pl"})

        pl_link = [html.unescape(href) for href in re.findall(r'<a href="([^"]*)"(?: aria-current="true")?>PL</a>', resp.text)]
        en_link = [html.unescape(href) for href in re.findall(r'<a href="([^"]*)"(?: aria-current="true")?>EN</a>', resp.text)]
        tag_links = [html.unescape(href) for href in re.findall(r'<a class="blog-tag[^"]*"[^>]*href="([^"]*)"', resp.text)]
        clear_links = _anchor_hrefs(resp.text, "wyczyść filtr")
        assert [_query_of(href) for href in pl_link] == [{"tag": ["x"], "lang": ["pl"]}]
        assert [_query_of(href) for href in en_link] == [{"tag": ["x"], "lang": ["en"]}]
        assert [_query_of(href) for href in tag_links] == [{"tag": ["x"], "lang": ["pl"]}]
        assert [_query_of(href) for href in clear_links] == [{"lang": ["pl"]}]


@pytest.mark.integration
class TestBlogIndexCards:
    """Karty listy: okładka, tagi i czas czytania; błąd storage degraduje kartę, nie listę."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_card_shows_cover_tags_and_reading_time_only_when_present(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = _words(250)
        project, user = await _scene(db_session, "card-full")
        full = await _create_wiki_page(db_session, project, user, "Karta Pelna MON260", is_public=True, description="Opis karty.", tags=["alpha-tag"])
        await _create_wiki_page(db_session, project, user, "Karta Pusta MON260", is_public=True, description="Opis karty pustej.")
        att = await _create_attachment(db_session, full, filename="okladka.png", mime_type="image/png")
        (await _blog_post_of(db_session, full.id)).cover_attachment_id = att.id
        await db_session.flush()

        resp = await client.get("/blog/")

        cards = _cards(resp.text)
        assert resp.status_code == 200
        assert len(cards) == 2
        full_card = next(card for card in cards if "Karta Pelna MON260" in card)
        empty_card = next(card for card in cards if "Karta Pusta MON260" in card)
        assert '<img class="blog-card-cover' in full_card
        assert f"/blog/{full.slug}/attachments/{att.id}/okladka.png" in full_card
        assert re.search(r'<a class="blog-tag(?:\s[^"]*)?"[^>]*href="/blog/\?tag=alpha-tag', full_card)
        assert "2 min read" in _after(full_card, 'class="blog-reading-time')
        assert '<img class="blog-card-cover' not in empty_card
        assert 'class="blog-tag' not in empty_card
        assert "2 min read" in _after(empty_card, 'class="blog-reading-time')

    @patch("monolynx.services.wiki.get_markdown")
    async def test_storage_error_for_one_card_drops_only_its_reading_time(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "card-storage")
        broken_path = f"{project.slug}/broken-{uuid.uuid4()}.md"

        def fake_get_markdown(path):
            if path == broken_path:
                raise Exception("MinIO niedostepne")
            return _words(250)

        mock_get_markdown.side_effect = fake_get_markdown
        await _create_wiki_page(
            db_session, project, user, "Karta Zepsuta MON260", minio_path=broken_path, is_public=True, description="Opis zepsutej."
        )
        await _create_wiki_page(db_session, project, user, "Karta Sprawna MON260", is_public=True, description="Opis sprawnej.")

        resp = await client.get("/blog/")

        cards = _cards(resp.text)
        assert resp.status_code == 200
        broken_card = next(card for card in cards if "Karta Zepsuta MON260" in card)
        healthy_card = next(card for card in cards if "Karta Sprawna MON260" in card)
        assert 'class="blog-reading-time' not in broken_card
        assert "Opis zepsutej." in broken_card
        assert "2 min read" in _after(healthy_card, 'class="blog-reading-time')


@pytest.mark.integration
class TestBlogConditionalResponses:
    """`ETag` / `Last-Modified` i `304` na stronie wpisu i na liście."""

    @patch("monolynx.services.wiki.get_markdown")
    async def test_post_returns_validators_and_304_with_vary_and_link(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc cache MON260."
        project, user = await _scene(db_session, "cache-post")
        page = await _create_wiki_page(
            db_session, project, user, "Wpis Cache MON260", is_public=True, updated_at=datetime(2026, 2, 3, 10, 0, tzinfo=UTC)
        )

        first = await client.get(f"/blog/{page.slug}")
        second = await client.get(f"/blog/{page.slug}", headers={"If-None-Match": first.headers["etag"]})

        assert first.status_code == 200
        assert first.headers["last-modified"] == "Tue, 03 Feb 2026 10:00:00 GMT"
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == first.headers["etag"]
        assert second.headers["vary"] == "Accept"
        assert 'type="text/markdown"' in second.headers["link"]
        assert f"/blog/{page.slug}.md" in second.headers["link"]

    @patch("monolynx.services.wiki.get_markdown")
    async def test_content_change_changes_etag_and_old_etag_no_longer_matches(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc pierwsza MON260."
        project, user = await _scene(db_session, "cache-change")
        page = await _create_wiki_page(db_session, project, user, "Wpis Zmiana Cache MON260", is_public=True)
        first = await client.get(f"/blog/{page.slug}")

        mock_get_markdown.return_value = "Tresc druga MON260."
        second = await client.get(f"/blog/{page.slug}", headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 200
        assert second.headers["etag"] != first.headers["etag"]

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_list_returns_validators_with_newest_page_update_and_304(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "cache-list")
        # Najpóźniejszy updated_at ma wpis opublikowany wcześniej, więc nie jest pierwszą kartą.
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lista Nowszy Cache MON260",
            is_public=True,
            published_at=_BASE_DAY + timedelta(days=9),
            updated_at=datetime(2026, 2, 3, 10, 0, tzinfo=UTC),
        )
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lista Starszy Cache MON260",
            is_public=True,
            published_at=_BASE_DAY + timedelta(days=1),
            updated_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
        )

        first = await client.get("/blog/")
        second = await client.get("/blog/", headers={"If-None-Match": first.headers["etag"]})

        assert first.status_code == 200
        assert first.headers["last-modified"] == "Thu, 05 Mar 2026 10:00:00 GMT"
        assert second.status_code == 304
        assert second.content == b""

    async def test_empty_list_uses_epoch_as_last_modified(self, client, db_session):
        resp = await client.get("/blog/")

        assert resp.status_code == 200
        assert resp.headers["last-modified"] == "Thu, 01 Jan 1970 00:00:00 GMT"
        assert resp.headers["etag"]

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_new_post_changes_list_etag(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "cache-list-new")
        await _create_wiki_page(db_session, project, user, "Wpis Lista Pierwszy MON260", is_public=True, description="Opis.")
        first = await client.get("/blog/")
        await _create_wiki_page(db_session, project, user, "Wpis Lista Drugi MON260", is_public=True, description="Opis.")

        second = await client.get("/blog/", headers={"If-None-Match": first.headers["etag"]})

        assert second.status_code == 200
        assert second.headers["etag"] != first.headers["etag"]


@pytest.mark.integration
class TestBlogInterfaceLanguage:
    """Język interfejsu wpisu: `?lang=` (`pl`/`en`), a bez niego albo z wartością spoza zbioru - `lang` wpisu."""

    @pytest.mark.parametrize(
        ("post_lang", "query", "html_lang", "reading_text", "absent_text"),
        [
            ("pl", {}, "pl", "4 min czytania", "4 min read"),
            ("pl", {"lang": "en"}, "en", "4 min read", "4 min czytania"),
            ("en", {}, "en", "4 min read", "4 min czytania"),
            ("en", {"lang": "pl"}, "pl", "4 min czytania", "4 min read"),
            ("pl", {"lang": "de"}, "pl", "4 min czytania", "4 min read"),
        ],
        ids=["pl-default", "pl-forced-en", "en-default", "en-forced-pl", "pl-unknown-lang"],
    )
    @patch("monolynx.services.wiki.get_markdown")
    async def test_interface_language(self, mock_get_markdown, post_lang, query, html_lang, reading_text, absent_text, client, db_session):
        mock_get_markdown.return_value = _words(650)
        project, user = await _scene(db_session, f"ui-{post_lang}-{query.get('lang', 'none')}")
        forced = query.get("lang", "none")
        page = await _create_wiki_page(db_session, project, user, f"Wpis Interfejs {post_lang} {forced} MON260", is_public=True, lang=post_lang)

        resp = await client.get(f"/blog/{page.slug}", params=query)

        assert resp.status_code == 200
        assert f'<html lang="{html_lang}"' in resp.text
        assert reading_text in _after(resp.text, 'class="blog-reading-time')
        assert absent_text not in resp.text

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_navbar_language_switch_points_to_the_other_language_of_the_post(self, mock_get_markdown, client, db_session):
        """Navbar pokazuje link tylko do drugiego języka: wpis `pl` -> `/blog/<slug>?lang=en`, z `?lang=en` -> `/blog/<slug>?lang=pl`;
        domyślne adresy layoutu (`/?lang=..`) nie występują."""
        project, user = await _scene(db_session, "ui-switch")
        page = await _create_wiki_page(db_session, project, user, "Wpis Przelacznik Jezyka MON260", is_public=True, lang="pl")

        default_hrefs = _hrefs((await client.get(f"/blog/{page.slug}")).text)
        forced_hrefs = _hrefs((await client.get(f"/blog/{page.slug}", params={"lang": "en"})).text)

        assert f"/blog/{page.slug}?lang=en" in default_hrefs
        assert "/?lang=en" not in default_hrefs
        assert f"/blog/{page.slug}?lang=pl" in forced_hrefs
        assert "/?lang=pl" not in forced_hrefs


@pytest.mark.integration
class TestBlogPageStyles:
    """Kryterium 8: typografia w `static/blog/blog.css`, nie w inline `<style>`; `blog.css` przed `blocks.css`."""

    def test_page_template_has_no_inline_style_block(self):
        assert "<style" not in _TEMPLATE_PAGE.read_text(encoding="utf-8")

    @patch("monolynx.services.wiki.get_markdown", return_value="Tresc MON260.")
    async def test_blog_css_is_linked_before_blocks_css(self, mock_get_markdown, client, db_session):
        project, user = await _scene(db_session, "styles-order")
        page = await _create_wiki_page(db_session, project, user, "Wpis Style MON260", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        text = resp.text
        blog_css = '<link rel="stylesheet" href="/static/blog/blog.css">'
        blocks_css = '<link rel="stylesheet" href="/static/blog/blocks.css">'
        assert blog_css in text
        assert blocks_css in text
        assert text.index(blog_css) < text.index(blocks_css)

    async def test_blog_css_is_served(self, client):
        resp = await client.get("/static/blog/blog.css")

        assert resp.status_code == 200
        assert "css" in resp.headers["content-type"]
        assert len(resp.content) > 0
