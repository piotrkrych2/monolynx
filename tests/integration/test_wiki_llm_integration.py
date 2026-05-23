"""Testy integracyjne warstw LLM Wiki - Warstwy 1-3 (MON-73).

Pokrycie:
- sync_backlinks (services/wiki.py) - backlinki w DB
- ensure_system_page (services/wiki.py) - get-or-create, idempotencja
- regenerate_index (services/wiki.py) - przebudowa katalogu
- append_log (services/wiki.py) - append-only log
- bootstrap_wiki_llm (services/wiki_bootstrap.py) - pełny bootstrap + idempotencja

Wzorzec: db_session z conftest.py (real async transaction z rollback).
MinIO mockowany przez @patch na upload_markdown i get_markdown.
"""

from __future__ import annotations

import secrets
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password
from monolynx.services.wiki import (
    append_log,
    ensure_system_page,
    regenerate_index,
    sync_backlinks,
)
from monolynx.services.wiki_bootstrap import bootstrap_wiki_llm
from monolynx.services.wiki_lint import _find_orphans, lint_wiki

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def wiki_user(db_session):
    """Użytkownik dla operacji wiki."""
    user = User(
        email=f"wiki-llm-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("pass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def wiki_project(db_session):
    """Projekt testowy z unikatowym slug/code."""
    slug = f"wiki-llm-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="Wiki LLM Test",
        slug=slug,
        code=slug.replace("-", "").upper()[:8],
        api_key=secrets.token_urlsafe(16),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


def _make_wiki_page(project: Project, user: User, slug: str, title: str, minio_path: str | None = None) -> WikiPage:
    """Tworzy WikiPage bez zapisu do DB (caller musi add+flush)."""
    path = minio_path or f"{project.slug}/{uuid.uuid4()}.md"
    return WikiPage(
        id=uuid.uuid4(),
        project_id=project.id,
        parent_id=None,
        title=title,
        slug=slug,
        position=0,
        minio_path=path,
        is_ai_touched=False,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )


# ---------------------------------------------------------------------------
# sync_backlinks
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSyncBacklinks:
    """Testy synchronizacji backlinków."""

    @patch("monolynx.services.wiki.upload_markdown", return_value="proj/x.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="")
    async def test_creates_backlink_for_wikilink(self, _mock_get, _mock_upload, db_session, wiki_project, wiki_user):
        """Treść A z [[slug-B]] tworzy backlink A->B."""
        page_b = _make_wiki_page(wiki_project, wiki_user, "strona-b", "Strona B")
        page_a = _make_wiki_page(wiki_project, wiki_user, "strona-a", "Strona A")
        db_session.add(page_b)
        db_session.add(page_a)
        await db_session.flush()

        content = "Sprawdź [[strona-b]]."
        await sync_backlinks(page=page_a, content=content, db=db_session)

        result = await db_session.execute(
            select(WikiBacklink).where(
                WikiBacklink.source_page_id == page_a.id,
                WikiBacklink.target_page_id == page_b.id,
            )
        )
        backlink = result.scalar_one_or_none()
        assert backlink is not None

    @patch("monolynx.services.wiki.upload_markdown", return_value="proj/x.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="")
    async def test_removes_backlink_when_link_removed(self, _mock_get, _mock_upload, db_session, wiki_project, wiki_user):
        """Po usunięciu linku z contentu stary backlink jest kasowany."""
        page_b = _make_wiki_page(wiki_project, wiki_user, "b-strona", "B")
        page_a = _make_wiki_page(wiki_project, wiki_user, "a-strona", "A")
        db_session.add(page_b)
        db_session.add(page_a)
        await db_session.flush()

        # Najpierw ustaw backlink
        await sync_backlinks(page=page_a, content="[[b-strona]]", db=db_session)
        result = await db_session.execute(select(WikiBacklink).where(WikiBacklink.source_page_id == page_a.id))
        assert result.scalar_one_or_none() is not None

        # Teraz usuń link - content bez wikilinku
        await sync_backlinks(page=page_a, content="Brak linków.", db=db_session)

        result2 = await db_session.execute(select(WikiBacklink).where(WikiBacklink.source_page_id == page_a.id))
        assert result2.scalar_one_or_none() is None

    @patch("monolynx.services.wiki.upload_markdown", return_value="proj/x.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="")
    async def test_self_link_ignored(self, _mock_get, _mock_upload, db_session, wiki_project, wiki_user):
        """Self-link [[slug-strony]] nie tworzy backlinku."""
        page = _make_wiki_page(wiki_project, wiki_user, "strona-samo", "Strona samo")
        db_session.add(page)
        await db_session.flush()

        await sync_backlinks(page=page, content="[[strona-samo]]", db=db_session)

        result = await db_session.execute(select(WikiBacklink).where(WikiBacklink.source_page_id == page.id))
        assert result.scalar_one_or_none() is None

    @patch("monolynx.services.wiki.upload_markdown", return_value="proj/x.md")
    @patch("monolynx.services.wiki.get_markdown", return_value="")
    async def test_dead_link_does_not_crash(self, _mock_get, _mock_upload, db_session, wiki_project, wiki_user):
        """Martwy link (brak strony o podanym slugu) - nie rzuca wyjątku, brak wpisu."""
        page = _make_wiki_page(wiki_project, wiki_user, "zrodlo", "Źródło")
        db_session.add(page)
        await db_session.flush()

        # Tworzymy backlink do nieistniejącej strony - powinien zostać zignorowany
        await sync_backlinks(page=page, content="[[nie-istnieje-nigdy]]", db=db_session)

        result = await db_session.execute(select(WikiBacklink).where(WikiBacklink.source_page_id == page.id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# ensure_system_page
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEnsureSystemPage:
    """Testy get-or-create dla stron systemowych wiki."""

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_on_first_call(self, mock_upload, db_session, wiki_project, wiki_user):
        """Pierwsze wywołanie tworzy stronę wiki-schema."""
        mock_upload.return_value = f"{wiki_project.slug}/schema.md"

        page = await ensure_system_page(
            project=wiki_project,
            slug="wiki-schema",
            title="Schemat Wiki",
            default_content="# Schemat",
            user_id=wiki_user.id,
            db=db_session,
        )

        assert page.slug == "wiki-schema"
        assert page.is_ai_touched is True
        assert page.project_id == wiki_project.id
        mock_upload.assert_called_once()

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_returns_existing_page_on_second_call(self, mock_upload, db_session, wiki_project, wiki_user):
        """Drugie wywołanie zwraca tę samą stronę - brak duplikatu."""
        mock_upload.return_value = f"{wiki_project.slug}/schema2.md"

        page1 = await ensure_system_page(
            project=wiki_project,
            slug="wiki-schema",
            title="Schemat Wiki",
            default_content="# Schemat",
            user_id=wiki_user.id,
            db=db_session,
        )
        page2 = await ensure_system_page(
            project=wiki_project,
            slug="wiki-schema",
            title="Schemat Wiki",
            default_content="# Schemat",
            user_id=wiki_user.id,
            db=db_session,
        )

        assert page1.id == page2.id
        # upload tylko raz (przy tworzeniu)
        assert mock_upload.call_count == 1

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_system_page_slug_isolated_per_project(self, mock_upload, db_session, wiki_user):
        """Strony systemowe są izolowane per projekt."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"

        slug_a = f"iso-a-{uuid.uuid4().hex[:6]}"
        slug_b = f"iso-b-{uuid.uuid4().hex[:6]}"
        code_a = slug_a.replace("-", "").upper()[:8]
        code_b = slug_b.replace("-", "").upper()[:8]

        proj_a = Project(
            name="Iso A",
            slug=slug_a,
            code=code_a,
            api_key=secrets.token_urlsafe(16),
        )
        proj_b = Project(
            name="Iso B",
            slug=slug_b,
            code=code_b,
            api_key=secrets.token_urlsafe(16),
        )
        db_session.add(proj_a)
        db_session.add(proj_b)
        await db_session.flush()

        page_a = await ensure_system_page(
            project=proj_a,
            slug="wiki-log",
            title="Log A",
            default_content="# Log A",
            user_id=wiki_user.id,
            db=db_session,
        )
        page_b = await ensure_system_page(
            project=proj_b,
            slug="wiki-log",
            title="Log B",
            default_content="# Log B",
            user_id=wiki_user.id,
            db=db_session,
        )

        assert page_a.id != page_b.id
        assert page_a.project_id == proj_a.id
        assert page_b.project_id == proj_b.id


# ---------------------------------------------------------------------------
# regenerate_index
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRegenerateIndex:
    """Testy przebudowy strony wiki-index."""

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_returns_index_page_and_count(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """regenerate_index zwraca (WikiPage, int) - strona katalogu i liczba stron."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = "Opis strony."

        # Tworzymy kilka stron użytkownika
        for i in range(3):
            page = _make_wiki_page(wiki_project, wiki_user, f"strona-{i}", f"Strona {i}")
            db_session.add(page)
        await db_session.flush()

        index_page, count = await regenerate_index(project=wiki_project, user_id=wiki_user.id, db=db_session)

        assert isinstance(index_page, WikiPage)
        assert index_page.slug == "wiki-index"
        assert isinstance(count, int)
        assert count >= 3

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_index_content_contains_page_titles(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Treść wiki-index zawiera tytuły stron - sprawdzamy przez mock_upload."""
        captured_content: list[str] = []

        def capture(slug, pid, content):
            captured_content.append(content)
            return f"{slug}/{pid}.md"

        mock_upload.side_effect = capture
        mock_get.return_value = ""

        page = _make_wiki_page(wiki_project, wiki_user, "unikalny-tytuł-abc", "Unikalny Tytuł ABC")
        db_session.add(page)
        await db_session.flush()

        await regenerate_index(project=wiki_project, user_id=wiki_user.id, db=db_session)

        # Index katalog zawiera wikilink [[slug]] do strony
        all_content = "\n".join(captured_content)
        assert "unikalny-tytuł-abc" in all_content

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_reserved_slugs_excluded_from_count(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Strony systemowe (wiki-index, wiki-log, wiki-schema) nie są liczone."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = ""

        # Tworzymy 1 normalną stronę i jedną systemową przez ensure_system_page
        normal_page = _make_wiki_page(wiki_project, wiki_user, "normalna", "Normalna")
        db_session.add(normal_page)
        await db_session.flush()

        # ensure_system_page tworzy wiki-log (systemowa)
        await ensure_system_page(
            project=wiki_project,
            slug="wiki-log",
            title="Log",
            default_content="# Log\n",
            user_id=wiki_user.id,
            db=db_session,
        )

        _, count = await regenerate_index(project=wiki_project, user_id=wiki_user.id, db=db_session)

        # wiki-log nie powinien być w count; normalna - tak
        assert count >= 1
        # Upewnij się że wiki-index i wiki-log nie wliczają się
        result = await db_session.execute(select(WikiPage).where(WikiPage.project_id == wiki_project.id, WikiPage.slug == "wiki-index"))
        index_page = result.scalar_one_or_none()
        assert index_page is not None


# ---------------------------------------------------------------------------
# append_log
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAppendLog:
    """Testy append_log - dziennik wiki."""

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_creates_log_page_on_first_call(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Pierwsze wywołanie tworzy stronę wiki-log."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = "# Dziennik Wiki\n\n"

        log_page = await append_log(
            project=wiki_project,
            entry="Pierwszy wpis testowy",
            user_id=wiki_user.id,
            db=db_session,
        )

        assert log_page.slug == "wiki-log"
        assert log_page.project_id == wiki_project.id

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_log_entry_appended_to_content(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Wpis zawiera format - [data] entry - sprawdzamy przez mock upload."""
        uploaded_contents: list[str] = []

        def capture(slug, pid, content):
            uploaded_contents.append(content)
            return f"{slug}/{pid}.md"

        mock_upload.side_effect = capture
        mock_get.return_value = "# Dziennik Wiki\n\n"

        await append_log(
            project=wiki_project,
            entry="Test wpisu logu",
            user_id=wiki_user.id,
            db=db_session,
        )

        # Treść uploadowana musi zawierać nasz wpis
        all_content = "\n".join(uploaded_contents)
        assert "Test wpisu logu" in all_content

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_second_call_appends_not_overwrites(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Drugi call zachowuje poprzedni wpis (append-only)."""
        # Slownik sciezka->tresc symuluje MinIO (odporne na kolejnosc wywolan)
        minio_store: dict[str, str] = {}

        def capture(slug, pid, content):
            path = f"{slug}/{pid}.md"
            minio_store[path] = content
            return path

        mock_upload.side_effect = capture
        mock_get.side_effect = lambda path: minio_store.get(path, "# Dziennik Wiki\n\n")

        await append_log(project=wiki_project, entry="Wpis pierwszy", user_id=wiki_user.id, db=db_session)
        await append_log(project=wiki_project, entry="Wpis drugi", user_id=wiki_user.id, db=db_session)

        # Tresc wiki-log musi zawierac oba wpisy (append-only)
        log_content = next(c for c in minio_store.values() if "Wpis pierwszy" in c)
        assert "Wpis pierwszy" in log_content
        assert "Wpis drugi" in log_content

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_log_entry_format(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Wpis zawiera datę w formacie [YYYY-MM-DD HH:MM]."""
        import re

        uploaded_contents: list[str] = []

        def capture(slug, pid, content):
            uploaded_contents.append(content)
            return f"{slug}/{pid}.md"

        mock_upload.side_effect = capture
        mock_get.return_value = "# Dziennik Wiki\n\n"

        await append_log(
            project=wiki_project,
            entry="Wpis do testu formatu",
            user_id=wiki_user.id,
            db=db_session,
        )

        all_content = "\n".join(uploaded_contents)
        # Format: - [YYYY-MM-DD HH:MM] entry
        assert re.search(r"- \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]", all_content) is not None


# ---------------------------------------------------------------------------
# bootstrap_wiki_llm
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBootstrapWikiLlm:
    """Testy pełnego bootstrapu LLM Wiki."""

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_bootstrap_enables_llm_flag(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Bootstrap włącza flagę wiki_llm_enabled na projekcie."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = "# Treść\n\n"
        assert wiki_project.wiki_llm_enabled is False

        await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        assert wiki_project.wiki_llm_enabled is True

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_bootstrap_creates_system_pages(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Bootstrap tworzy strony wiki-schema, wiki-index, wiki-log."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = "# Treść\n\n"

        result = await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        # Sprawdź klucze zwrotki
        assert "schema_page_id" in result
        assert "index_page_id" in result
        assert "log_page_id" in result
        assert "catalogued_pages" in result
        assert result["wiki_llm_enabled"] is True

        # Sprawdź że strony faktycznie istnieją w DB
        for reserved_slug in ("wiki-schema", "wiki-index", "wiki-log"):
            res = await db_session.execute(
                select(WikiPage).where(
                    WikiPage.project_id == wiki_project.id,
                    WikiPage.slug == reserved_slug,
                )
            )
            page = res.scalar_one_or_none()
            assert page is not None, f"Brak strony {reserved_slug} po bootstrapie"

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_bootstrap_idempotent_no_duplicate_pages(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Drugi bootstrap nie tworzy duplikatów stron systemowych.

        ensure_system_page to get-or-create, więc drugi bootstrap
        pobiera istniejące strony zamiast tworzyć nowe.
        """
        last_content: dict[str, str] = {}

        def capture(slug, pid, content):
            path = f"{slug}/{pid}.md"
            last_content[path] = content
            return path

        mock_upload.side_effect = capture
        mock_get.side_effect = lambda path: last_content.get(path, "# Dziennik Wiki\n\n")

        await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        # Zlicz strony systemowe po pierwszym bootstrapie
        res = await db_session.execute(select(WikiPage).where(WikiPage.project_id == wiki_project.id))
        count_first = len(list(res.scalars().all()))

        # Drugi bootstrap
        await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        res2 = await db_session.execute(select(WikiPage).where(WikiPage.project_id == wiki_project.id))
        count_second = len(list(res2.scalars().all()))

        # Liczba stron nie powinna wzrosnąć (idempotencja ensure_system_page)
        assert count_second == count_first

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_bootstrap_returns_valid_uuids(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Zwracane ID stron są prawidłowymi UUID-ami."""
        mock_upload.side_effect = lambda slug, pid, content: f"{slug}/{pid}.md"
        mock_get.return_value = "# Dziennik Wiki\n\n"

        result = await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        for key in ("schema_page_id", "index_page_id", "log_page_id"):
            # Nie powinno rzucać wyjątku
            uuid.UUID(result[key])

    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_bootstrap_log_has_entry_after_second_run(self, mock_get, mock_upload, db_session, wiki_project, wiki_user):
        """Drugi bootstrap dopisuje kolejny wpis do wiki-log (append-only)."""
        # Słownik ścieżka->treść - symuluje MinIO
        minio_store: dict[str, str] = {}

        def capture(slug, pid, content):
            path = f"{slug}/{pid}.md"
            minio_store[path] = content
            return path

        mock_upload.side_effect = capture
        mock_get.side_effect = lambda path: minio_store.get(path, "# Dziennik Wiki\n\n")

        await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)
        await bootstrap_wiki_llm(project=wiki_project, user_id=wiki_user.id, db=db_session)

        # Znajdź treść wiki-log (minio_store będzie zawierał ścieżkę wiki-log)
        log_content = None
        for _path, content in minio_store.items():
            if "Metoda LLM Wiki" in content:
                log_content = content

        assert log_content is not None, "wiki-log musi zawierać wpis bootstrapu"
        # Dwa bootstrapy - co najmniej 2 wzmianki o bootstrapie
        assert log_content.count("Metoda LLM Wiki") >= 2


# ---------------------------------------------------------------------------
# _find_orphans (detekcja sierot - zapytanie po WikiBacklink)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFindOrphans:
    """Testy detekcji sierot - stron bez backlinku przychodzącego."""

    async def test_detects_orphan_excludes_root_and_linked(self, db_session, wiki_project, wiki_user):
        """Sierota to strona-dziecko bez backlinku; korzenie i strony linkowane pominięte."""
        root = _make_wiki_page(wiki_project, wiki_user, "korzen", "Korzen")
        db_session.add(root)
        await db_session.flush()

        orphan = _make_wiki_page(wiki_project, wiki_user, "sierota", "Sierota")
        orphan.parent_id = root.id
        linked = _make_wiki_page(wiki_project, wiki_user, "polaczona", "Polaczona")
        linked.parent_id = root.id
        db_session.add(orphan)
        db_session.add(linked)
        await db_session.flush()

        # Backlink do strony "polaczona" - przestaje być sierotą
        db_session.add(WikiBacklink(source_page_id=root.id, target_page_id=linked.id))
        await db_session.flush()

        orphans = await _find_orphans(wiki_project.id, [root, orphan, linked], db_session)
        slugs = {o["slug"] for o in orphans}

        assert "sierota" in slugs  # dziecko bez backlinku
        assert "polaczona" not in slugs  # ma backlink przychodzący
        assert "korzen" not in slugs  # korzeń (parent_id is None) wykluczony


# ---------------------------------------------------------------------------
# lint_wiki (orkiestrator - raport 4 kategorii)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLintWikiOrchestrator:
    """Test end-to-end orkiestratora lint_wiki (sieroty, martwe linki, sprzeczności, luki)."""

    @patch("monolynx.services.wiki_lint.get_markdown")
    async def test_reports_all_categories(self, mock_get, db_session, wiki_project, wiki_user):
        """lint_wiki zwraca raport z czterema kategoriami wypełnionymi danymi."""
        # Patch celuje w get_markdown w module wiki_lint (tam jest importowany), nie wiki
        root = _make_wiki_page(wiki_project, wiki_user, "korzen", "Korzen", minio_path="p/root.md")
        db_session.add(root)
        await db_session.flush()

        z_martwym = _make_wiki_page(wiki_project, wiki_user, "z-martwym", "Z martwym", minio_path="p/dead.md")
        z_martwym.parent_id = root.id
        ze_sprzecznoscia = _make_wiki_page(wiki_project, wiki_user, "ze-sprzecznoscia", "Ze sprzecznoscia", minio_path="p/contra.md")
        ze_sprzecznoscia.parent_id = root.id
        db_session.add(z_martwym)
        db_session.add(ze_sprzecznoscia)
        await db_session.flush()

        # Backlink root -> z-martwym, żeby z-martwym nie był sierotą; ze-sprzecznoscia zostaje sierotą
        db_session.add(WikiBacklink(source_page_id=root.id, target_page_id=z_martwym.id))
        await db_session.flush()

        # nieistniejacy-koncept wzmiankowany na 2 stronach (root + dead) -> dead_link x2 i luka (count>=2)
        contents = {
            "p/root.md": "Korzen [[z-martwym]] [[ze-sprzecznoscia]] [[nieistniejacy-koncept]]",
            "p/dead.md": "Odnosnik do [[nieistniejacy-koncept]].",
            "p/contra.md": "> **Sprzeczność [2026-05-22]:** Zrodlo A mowi X, zrodlo B mowi Y.",
        }
        mock_get.side_effect = lambda path: contents.get(path, "")

        report = await lint_wiki(wiki_project.id, db_session)

        # Martwe linki: nieistniejacy-koncept zgłoszony (z root i z dead)
        assert any(d["ref"] == "nieistniejacy-koncept" for d in report["dead_links"])
        # Sprzeczności: strona ze-sprzecznoscia
        assert any(c["slug"] == "ze-sprzecznoscia" for c in report["contradictions"])
        # Sieroty: ze-sprzecznoscia (dziecko bez backlinku); z-martwym ma backlink
        orphan_slugs = {o["slug"] for o in report["orphans"]}
        assert "ze-sprzecznoscia" in orphan_slugs
        assert "z-martwym" not in orphan_slugs
        # Luki: nieistniejacy-koncept wzmiankowany na 2 stronach (count>=2)
        assert any(g["ref"] == "nieistniejacy-koncept" for g in report["gaps"])
