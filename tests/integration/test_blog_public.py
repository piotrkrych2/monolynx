"""Testy integracyjne MON-201 -- publiczny endpoint GET /blog/{slug}.

WAZNE: endpoint /blog/{slug} dziala BEZ sesji i BEZ project_slug w URL. Zaden test
w tym pliku nie uzywa login_session -- goly `client` bez logowania potwierdza dostep
anonimowy. To jest sedno kryteriow akceptacji tego ticketu.

Zanim implementacja powstanie (src/monolynx/blog.py, kolumna is_public, migracja),
wszystkie testy w tym pliku musza zawodzic (brak routera /blog, brak kolumny is_public
na WikiPage) -- to jest zamierzone, TDD.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from monolynx.config import settings
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password
from monolynx.services.wiki import create_wiki_page, update_wiki_page

# ---------------------------------------------------------------------------
# Helpers lokalne (konwencja z tests/integration/test_wiki_dashboard.py,
# ale bez importu login_session -- endpoint publiczny nie wymaga sesji)
# ---------------------------------------------------------------------------


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
    """Helper: tworzy uzytkownika w DB (bez logowania -- endpoint publiczny go nie wymaga)."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _create_wiki_page(
    db_session,
    project,
    user,
    title,
    minio_path=None,
    parent_id=None,
    is_public=False,
    updated_at=None,
):
    """Helper: tworzy strone wiki bezposrednio w DB (bez realnego MinIO).

    updated_at: jawne przypisanie PRZED pierwszym flush (INSERT) nadpisuje
    server_default -- potrzebne do deterministycznego sortowania w testach listy
    /blog/ (wzorzec MON-87, patrz .claude/agent-memory/qa-tester/MEMORY.md).
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
    db_session.add(page)
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


# ---------------------------------------------------------------------------
# MON-202: GET /blog/ -- lista publicznych wpisow z paginacja (kryterium 1)
#
# Endpoint /blog/ jeszcze nie istnieje -- ponizsze testy dzis dostaja 404, wiec sa
# RED przed implementacja. To jest zamierzone, TDD.
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
    updated_at malejaco, `page` z clampingiem do <1, total_pages>, bez 500 na zly input.

    Kolejnosc wstawiania (i=0..24) jest CELOWO odwrotna wzgledem oczekiwanej
    kolejnosci malejacej: i=0 ma NAJSTARSZY updated_at, i=24 NAJNOWSZY. Gdyby
    implementacja nie sortowala wcale albo sortowala rosnaco, Postgres zwrocilby
    wiersze w kolejnosci fizycznej = kolejnosci wstawiania (00, 01, 02, ...) i
    strona 1 zawieralaby "00" zamiast "24" -- ponizsze asercje wtedy PADAJA (w
    odroznieniu od wczesniejszej wersji tego seeda, gdzie kolejnosc wstawiania
    pokrywala sie z oczekiwana kolejnoscia malejaca i test przechodzil nawet bez
    ORDER BY -- MON-202 code review, regula z MON-191/MON-197).

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
                updated_at=base + timedelta(minutes=i),
            )

    @patch("monolynx.services.wiki.get_markdown")
    async def test_first_page_has_most_recently_updated_not_oldest(self, mock_get_markdown, client, db_session):
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
    async def test_og_type_is_article_and_published_time_matches_created_at(self, mock_get_markdown, client, db_session):
        mock_get_markdown.return_value = "Tresc artykulu MON202."

        project = await _create_project(db_session, "blog-meta-ogtype")
        user = await _create_user(db_session, "blog-meta-ogtype@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Og Type MON202", is_public=True)

        resp = await client.get(f"/blog/{page.slug}")

        assert resp.status_code == 200
        assert 'property="og:type" content="article"' in resp.text
        assert f'property="article:published_time" content="{page.created_at.isoformat()}"' in resp.text


@pytest.mark.integration
class TestLandingOgTypeStillWebsite:
    """Regresja: strony spoza /blog/ (np. landing) NADAL maja og:type=website --
    dodanie og:type=article dla wpisow bloga nie moze zepsuc reszty serwisu."""

    async def test_landing_page_og_type_is_still_website(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SKIP_LANDING_PAGE", False)

        resp = await client.get("/?lang=en")

        assert resp.status_code == 200
        assert 'property="og:type" content="website"' in resp.text
