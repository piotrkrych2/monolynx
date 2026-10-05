"""Testy integracyjne sitemap.xml z bazy (wpisy bloga) i robots.txt (jawne Allow: /blog/).

MON-254: adresy wpisów pochodzą z `BlogPost.slug` (nie ze sluga strony wiki), a `lastmod`
z `BlogPost.last_verified_at`, potem `BlogPost.published_at`, potem `WikiPage.updated_at`.
Sitemap zawiera też stronę listy `{base}/blog/`.
"""

import secrets
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime

import pytest

from monolynx.config import settings
from monolynx.features import feature_slugs
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# ---------------------------------------------------------------------------
# Helpers lokalne (konwencja z tests/integration/test_blog_public.py)
# ---------------------------------------------------------------------------


async def _create_project(db_session, slug, code=None):
    """Helper: tworzy projekt z unikalnym kodem."""
    if code is None:
        code = "S" + secrets.token_hex(4).upper()
    project = Project(
        name=f"Sitemap {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user(db_session, email):
    """Helper: tworzy uzytkownika w DB."""
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=False)
    db_session.add(user)
    await db_session.flush()
    return user


async def _create_wiki_page(
    db_session,
    project,
    user,
    title,
    is_public=False,
    updated_at=None,
    *,
    with_post=None,
    post_slug=None,
    published_at=None,
    last_verified_at=None,
):
    """Helper: tworzy strone wiki (bez realnego MinIO) i jej BlogPost.

    updated_at: jawne przypisanie PRZED pierwszym flush (INSERT) nadpisuje
    server_default -- potrzebne do deterministycznego lastmod w testach sitemap.
    with_post: domyslnie BlogPost powstaje dla stron publicznych; True wymusza go tez dla
    prywatnej (wpis cofniety z publikacji).
    """
    page = WikiPage(
        project_id=project.id,
        title=title,
        slug=title.lower().replace(" ", "-"),
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    if updated_at is not None:
        page.updated_at = updated_at
    db_session.add(page)
    await db_session.flush()
    if with_post if with_post is not None else is_public:
        post = BlogPost(
            page_id=page.id,
            slug=post_slug or page.slug,
            published_at=published_at,
            last_verified_at=last_verified_at,
        )
        db_session.add(post)
        await db_session.flush()
    return page


def _sitemap_entries(xml_text: str) -> dict[str, str | None]:
    """Sparsuj sitemap.xml (walidacja poprawnosci XML -- rzuca ParseError jesli zle
    sformowany) i zwroc mape loc -> lastmod (None jesli wpis nie ma lastmod)."""
    root = ET.fromstring(xml_text)
    entries: dict[str, str | None] = {}
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc_el = url_el.find("sm:loc", _SITEMAP_NS)
        lastmod_el = url_el.find("sm:lastmod", _SITEMAP_NS)
        if loc_el is not None and loc_el.text:
            entries[loc_el.text] = lastmod_el.text if lastmod_el is not None else None
    return entries


# ---------------------------------------------------------------------------
# 1. sitemap.xml -- wpisy publiczne z lastmod (kryterium 4), bez prywatnych (kryterium 5)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSitemapPublicWikiPages:
    """sitemap.xml zawiera publiczne wpisy z <lastmod> i pomija prywatne."""

    async def test_sitemap_is_well_formed_xml(self, client):
        """Regresja: sitemap.xml pozostaje poprawnym XML (np. slug z & musialby byc
        escapowany) niezaleznie od tego ile wpisow z bazy do niego dopiszemy."""
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        ET.fromstring(resp.text)

    async def test_sitemap_includes_blog_index(self, client):
        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        assert f"{base}/blog/" in _sitemap_entries(resp.text)

    async def test_sitemap_includes_public_page_under_post_slug_not_page_slug(self, client, db_session):
        project = await _create_project(db_session, "sitemap-pub")
        user = await _create_user(db_session, "sitemap-pub@test.com")
        page = await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Sitemap Publiczny MON254",
            is_public=True,
            post_slug="adres-wpisu-w-sitemap-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        assert f"{base}/blog/adres-wpisu-w-sitemap-mon254" in entries
        assert f"{base}/blog/{page.slug}" not in entries

    async def test_sitemap_lastmod_is_published_at_when_no_last_verified_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-pub")
        user = await _create_user(db_session, "sitemap-lm-pub@test.com")
        published = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Published MON254",
            is_public=True,
            post_slug="lastmod-published-mon254",
            published_at=published,
            updated_at=datetime(2026, 5, 5, 5, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-published-mon254"] == published.isoformat()

    async def test_sitemap_lastmod_prefers_last_verified_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-ver")
        user = await _create_user(db_session, "sitemap-lm-ver@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Verified MON254",
            is_public=True,
            post_slug="lastmod-verified-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
            last_verified_at=date(2026, 3, 9),
            updated_at=datetime(2026, 5, 5, 5, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-verified-mon254"] == date(2026, 3, 9).isoformat()

    async def test_sitemap_lastmod_falls_back_to_page_updated_at(self, client, db_session):
        project = await _create_project(db_session, "sitemap-lm-upd")
        user = await _create_user(db_session, "sitemap-lm-upd@test.com")
        updated = datetime(2026, 5, 5, 5, 0, tzinfo=UTC)
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Lastmod Updated MON254",
            is_public=True,
            post_slug="lastmod-updated-mon254",
            updated_at=updated,
        )

        resp = await client.get("/sitemap.xml")

        base = settings.APP_URL.rstrip("/")
        assert _sitemap_entries(resp.text)[f"{base}/blog/lastmod-updated-mon254"] == updated.isoformat()

    async def test_sitemap_excludes_private_page(self, client, db_session):
        """Kryterium 5: strona nieoznaczona is_public nie pojawia sie w sitemap.xml."""
        project = await _create_project(db_session, "sitemap-priv")
        user = await _create_user(db_session, "sitemap-priv@test.com")
        page = await _create_wiki_page(db_session, project, user, "Wpis Sitemap Prywatny MON202", is_public=False)

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        assert f"{base}/blog/{page.slug}" not in entries
        assert "Wpis Sitemap Prywatny MON202" not in resp.text

    async def test_sitemap_excludes_private_page_that_has_blog_post(self, client, db_session):
        """Wpis cofniety z publikacji zachowuje BlogPost, ale nie moze trafic do sitemapy (ani pod slugiem, ani starym)."""
        project = await _create_project(db_session, "sitemap-priv-post")
        user = await _create_user(db_session, "sitemap-priv-post@test.com")
        await _create_wiki_page(
            db_session,
            project,
            user,
            "Wpis Cofniety MON254",
            is_public=False,
            with_post=True,
            post_slug="wpis-cofniety-adres-mon254",
            published_at=datetime(2026, 2, 1, 8, 0, tzinfo=UTC),
        )

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        assert "wpis-cofniety-adres-mon254" not in resp.text


# ---------------------------------------------------------------------------
# 2. sitemap.xml -- regresja: wszystkie dotychczasowe adresy /features/* zostaja
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSitemapStillContainsFeatureUrls:
    """Regresja: sitemap.xml zawiera nadal WSZYSTKIE dotychczasowe adresy /features/*
    (kryterium 4, druga polowa) -- wpisy z bazy sa DOPISYWANE obok, nie zastepuja."""

    async def test_all_feature_slugs_present_in_both_languages(self, client, db_session):
        project = await _create_project(db_session, "sitemap-features")
        user = await _create_user(db_session, "sitemap-features@test.com")
        await _create_wiki_page(db_session, project, user, "Wpis Obok Features MON202", is_public=True)

        resp = await client.get("/sitemap.xml")

        assert resp.status_code == 200
        base = settings.APP_URL.rstrip("/")
        entries = _sitemap_entries(resp.text)
        slugs = feature_slugs()
        assert slugs, "feature_slugs() nie powinno byc puste -- inaczej test nic nie sprawdza"
        for slug in slugs:
            assert f"{base}/features/{slug}?lang=en" in entries
            assert f"{base}/features/{slug}?lang=pl" in entries


# ---------------------------------------------------------------------------
# 3. robots.txt -- jawny Allow: /blog/, dotychczasowe Disallow zostaja (kryterium 6)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRobotsAllowsBlog:
    """robots.txt pozwala na /blog/ i nadal blokuje /dashboard/, /auth/, /api/, /mcp."""

    async def test_robots_has_explicit_allow_blog(self, client):
        resp = await client.get("/robots.txt")

        assert resp.status_code == 200
        assert "Allow: /blog/" in resp.text

    async def test_robots_still_blocks_protected_paths(self, client):
        """Regresja: dotychczasowe linie Disallow nie zostaly usuniete przy dodawaniu
        jawnego Allow: /blog/."""
        resp = await client.get("/robots.txt")

        assert resp.status_code == 200
        assert "Disallow: /dashboard/" in resp.text
        assert "Disallow: /auth/" in resp.text
        assert "Disallow: /api/" in resp.text
        assert "Disallow: /mcp" in resp.text
